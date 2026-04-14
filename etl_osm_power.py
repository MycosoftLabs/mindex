#!/usr/bin/env python3
"""
MINDEX OSM Power Infrastructure ETL — Bulk load from Geofabrik extracts

Downloads pre-filtered power infrastructure data from OSM Overpass API
using targeted queries per continent, then loads into MINDEX PostGIS.

For massive bulk loads, use Geofabrik PBF extracts + osmium filtering:
  1. Download: https://download.geofabrik.de/
  2. Filter: osmium tags-filter input.osm.pbf power=plant power=line power=substation -o power.osm.pbf
  3. Convert: ogr2ogr -f PostgreSQL PG:"..." power.osm.pbf

This script uses the Overpass API approach which is simpler but slower.
For production, switch to Geofabrik PBF bulk processing.

Usage:
  python etl_osm_power.py --plants        # Power plants only
  python etl_osm_power.py --substations   # Substations only
  python etl_osm_power.py --lines         # Transmission lines only
  python etl_osm_power.py --datacenters   # Data centers only
  python etl_osm_power.py --all           # Everything
"""

import asyncio
import json
import logging
import os
import sys
import time

import httpx
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("osm-power")

DB_DSN = "postgresql://mycosoft:mycosoft_mindex_2026@192.168.0.189:5432/mindex"

# Overpass API endpoints (rotate to avoid rate limits)
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
_endpoint_idx = 0


def next_overpass_url():
    global _endpoint_idx
    url = OVERPASS_ENDPOINTS[_endpoint_idx % len(OVERPASS_ENDPOINTS)]
    _endpoint_idx += 1
    return url


# Regional bounding boxes — smaller regions to avoid Overpass timeouts
REGIONS = [
    # North America
    ("US-West", "24,-130,50,-100"),
    ("US-East", "24,-100,50,-60"),
    ("Canada", "42,-140,70,-52"),
    ("Mexico", "14,-118,33,-86"),
    # Europe
    ("EU-West", "35,-12,60,15"),
    ("EU-East", "35,15,72,45"),
    ("UK", "49,-11,62,3"),
    # Asia
    ("East-Asia", "20,100,55,150"),
    ("South-Asia", "5,60,40,100"),
    ("Southeast-Asia", "-10,95,25,145"),
    ("Central-Asia", "30,45,55,90"),
    # South America
    ("SA-North", "-5,-82,13,-34"),
    ("SA-South", "-55,-82,-5,-34"),
    # Africa
    ("Africa-North", "15,-20,40,55"),
    ("Africa-South", "-40,-20,15,55"),
    # Oceania
    ("Australia", "-45,110,-10,155"),
    ("NZ-Pacific", "-50,160,-30,180"),
]


async def get_db():
    return await asyncpg.connect(DB_DSN)


async def overpass_query(client: httpx.AsyncClient, query: str, label: str) -> list:
    """Execute an Overpass query with automatic endpoint rotation and retry."""
    for attempt in range(3):
        url = next_overpass_url()
        try:
            log.info(f"  [{label}] Querying {url.split('/')[2]}...")
            resp = await client.post(url, data={"data": query}, timeout=180)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("elements", [])
            elif resp.status_code == 429:
                log.warning(f"  [{label}] Rate limited, waiting 10s...")
                await asyncio.sleep(10)
            elif resp.status_code == 504:
                log.warning(f"  [{label}] Timeout, trying next endpoint...")
                await asyncio.sleep(2)
            else:
                log.warning(f"  [{label}] HTTP {resp.status_code}")
                await asyncio.sleep(3)
        except Exception as e:
            log.warning(f"  [{label}] Error: {e}")
            await asyncio.sleep(3)
    return []


async def load_power_plants(conn: asyncpg.Connection):
    """Load all power plants from OSM globally."""
    log.info("=" * 60)
    log.info("Loading POWER PLANTS from OpenStreetMap")
    log.info("=" * 60)

    total = 0
    async with httpx.AsyncClient() as client:
        for region_name, bbox in REGIONS:
            query = f'[out:json][timeout:120][bbox:{bbox}];nwr["power"="plant"];out center 10000;'
            elements = await overpass_query(client, query, f"plants/{region_name}")

            if not elements:
                continue

            log.info(f"  [{region_name}] Got {len(elements)} power plants")
            inserted = 0

            for el in elements:
                lat = el.get("lat") or el.get("center", {}).get("lat")
                lng = el.get("lon") or el.get("center", {}).get("lon")
                if not lat or not lng:
                    continue

                tags = el.get("tags", {})
                name = tags.get("name", "Power Plant")
                source_type = tags.get("plant:source", tags.get("generator:source", "unknown"))
                capacity = tags.get("plant:output:electricity", "")
                operator = tags.get("operator")
                source_id = f"osm-{el.get('type', 'node')}-{el.get('id', 0)}"

                # Parse capacity to MW
                capacity_mw = 0
                if capacity:
                    try:
                        cap = capacity.lower().replace(" ", "")
                        if "gw" in cap:
                            capacity_mw = float(cap.replace("gw", "")) * 1000
                        elif "mw" in cap:
                            capacity_mw = float(cap.replace("mw", ""))
                        elif "kw" in cap:
                            capacity_mw = float(cap.replace("kw", "")) / 1000
                    except ValueError:
                        pass

                try:
                    await conn.execute("""
                        INSERT INTO infra.facilities (source, source_id, name, facility_type, sub_type,
                            location, operator, capacity, status, properties)
                        VALUES ('osm', $1, $2, 'power_plant', $3,
                            ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography,
                            $6, $7, 'active', $8::jsonb)
                        ON CONFLICT (source, source_id) DO UPDATE SET
                            name = EXCLUDED.name, capacity = EXCLUDED.capacity,
                            properties = EXCLUDED.properties
                    """,
                        source_id, name, source_type,
                        lng, lat, operator, str(capacity_mw) if capacity_mw else capacity,
                        json.dumps({**tags, "capacity_mw": capacity_mw}),
                    )
                    inserted += 1
                except Exception:
                    pass

            log.info(f"  [{region_name}] Inserted {inserted} power plants")
            total += inserted
            await asyncio.sleep(2)  # Rate limit between regions

    log.info(f"  TOTAL: {total} power plants loaded")
    return total


async def load_substations(conn: asyncpg.Connection):
    """Load all electrical substations from OSM globally."""
    log.info("=" * 60)
    log.info("Loading SUBSTATIONS from OpenStreetMap")
    log.info("=" * 60)

    total = 0
    async with httpx.AsyncClient() as client:
        for region_name, bbox in REGIONS:
            query = f'[out:json][timeout:120][bbox:{bbox}];nwr["power"="substation"];out center 10000;'
            elements = await overpass_query(client, query, f"subs/{region_name}")

            if not elements:
                continue

            log.info(f"  [{region_name}] Got {len(elements)} substations")
            inserted = 0

            for el in elements:
                lat = el.get("lat") or el.get("center", {}).get("lat")
                lng = el.get("lon") or el.get("center", {}).get("lon")
                if not lat or not lng:
                    continue

                tags = el.get("tags", {})
                voltage_str = tags.get("voltage", "0")
                try:
                    voltage_kv = max(float(v) / 1000 for v in voltage_str.replace(";", ",").split(",") if v.strip())
                except (ValueError, TypeError):
                    voltage_kv = 0

                source_id = f"osm-{el.get('type', 'node')}-{el.get('id', 0)}"
                name = tags.get("name", f"Substation {voltage_kv:.0f}kV")

                try:
                    await conn.execute("""
                        INSERT INTO infra.power_grid (source, source_id, asset_type, name, voltage_kv,
                            location, operator, properties)
                        VALUES ('osm', $1, 'substation', $2, $3,
                            ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography,
                            $6, $7::jsonb)
                        ON CONFLICT DO NOTHING
                    """,
                        source_id, name, voltage_kv,
                        lng, lat, tags.get("operator"),
                        json.dumps(tags),
                    )
                    inserted += 1
                except Exception:
                    pass

            log.info(f"  [{region_name}] Inserted {inserted} substations")
            total += inserted
            await asyncio.sleep(2)

    log.info(f"  TOTAL: {total} substations loaded")
    return total


async def load_datacenters(conn: asyncpg.Connection):
    """Load data centers from OSM globally."""
    log.info("=" * 60)
    log.info("Loading DATA CENTERS from OpenStreetMap")
    log.info("=" * 60)

    total = 0
    async with httpx.AsyncClient() as client:
        # Data centers are fewer, can query broader regions
        broad_regions = [
            ("Americas", "-60,-140,70,-30"),
            ("EMEA", "-40,-30,72,60"),
            ("APAC", "-50,60,70,180"),
        ]
        for region_name, bbox in broad_regions:
            query = f'[out:json][timeout:120][bbox:{bbox}];nwr["building"="data_centre"];out center 5000;'
            elements = await overpass_query(client, query, f"dc/{region_name}")

            # Also try telecom=data_center tag
            query2 = f'[out:json][timeout:120][bbox:{bbox}];nwr["telecom"="data_center"];out center 5000;'
            elements2 = await overpass_query(client, query2, f"dc-telecom/{region_name}")

            all_elements = elements + elements2
            if not all_elements:
                continue

            # Deduplicate by OSM ID
            seen = set()
            deduped = []
            for el in all_elements:
                key = f"{el.get('type')}-{el.get('id')}"
                if key not in seen:
                    seen.add(key)
                    deduped.append(el)

            log.info(f"  [{region_name}] Got {len(deduped)} data centers")
            inserted = 0

            for el in deduped:
                lat = el.get("lat") or el.get("center", {}).get("lat")
                lng = el.get("lon") or el.get("center", {}).get("lon")
                if not lat or not lng:
                    continue

                tags = el.get("tags", {})
                name = tags.get("name", "Data Center")
                operator = tags.get("operator")
                source_id = f"osm-{el.get('type', 'node')}-{el.get('id', 0)}"

                try:
                    await conn.execute("""
                        INSERT INTO infra.facilities (source, source_id, name, facility_type, sub_type,
                            location, operator, status, properties)
                        VALUES ('osm', $1, $2, 'data_center', 'data_center',
                            ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography,
                            $5, 'active', $6::jsonb)
                        ON CONFLICT (source, source_id) DO UPDATE SET
                            name = EXCLUDED.name, properties = EXCLUDED.properties
                    """,
                        source_id, name, lng, lat, operator,
                        json.dumps(tags),
                    )
                    inserted += 1
                except Exception:
                    pass

            log.info(f"  [{region_name}] Inserted {inserted} data centers")
            total += inserted
            await asyncio.sleep(2)

    log.info(f"  TOTAL: {total} data centers loaded")
    return total


async def load_submarine_cables(conn: asyncpg.Connection):
    """Load submarine cables from TeleGeography GitHub."""
    log.info("=" * 60)
    log.info("Loading SUBMARINE CABLES from TeleGeography")
    log.info("=" * 60)

    # Use the GeoJSON format which has coordinates
    url = "https://www.submarinecablemap.com/api/v3/cable/cable-geo.json"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        try:
            geojson = resp.json()
        except Exception:
            log.error("  Failed to parse submarine cable GeoJSON")
            return 0

    features = geojson.get("features", [])
    log.info(f"  Got {len(features)} submarine cable features")
    inserted = 0

    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        cable_id = props.get("id", str(hash(props.get("name", ""))))
        name = props.get("name", "Unknown Cable")
        color = props.get("color", "#06b6d4")

        coords = geom.get("coordinates", [])
        if not coords or geom.get("type") not in ("LineString", "MultiLineString"):
            continue

        # Handle MultiLineString — take the longest segment
        if geom["type"] == "MultiLineString":
            coords = max(coords, key=len) if coords else []

        if len(coords) < 2:
            continue

        # Build WKT LINESTRING
        coord_pairs = [f"{c[0]} {c[1]}" for c in coords if len(c) >= 2 and abs(c[0]) <= 180 and abs(c[1]) <= 90]
        if len(coord_pairs) < 2:
            continue
        linestring = f"LINESTRING({', '.join(coord_pairs)})"

        try:
            await conn.execute("""
                INSERT INTO infra.internet_cables (source, source_id, name, cable_type,
                    route, status, properties)
                VALUES ('submarinecablemap', $1, $2, 'submarine',
                    ST_GeogFromText($3), 'active',
                    $4::jsonb)
                ON CONFLICT DO NOTHING
            """,
                str(cable_id), name, linestring,
                json.dumps({"color": color, **props}),
            )
            inserted += 1
        except Exception as e:
            if inserted == 0:
                log.error(f"  Cable insert error: {e}")

    log.info(f"  Inserted {inserted} submarine cables")
    return inserted


async def load_military_bases(conn: asyncpg.Connection):
    """Load military installations from OSM."""
    log.info("=" * 60)
    log.info("Loading MILITARY INSTALLATIONS from OpenStreetMap")
    log.info("=" * 60)

    total = 0
    async with httpx.AsyncClient() as client:
        broad_regions = [
            ("Americas", "-60,-140,70,-30"),
            ("EMEA", "-40,-30,72,60"),
            ("APAC", "-50,60,70,180"),
        ]
        for region_name, bbox in broad_regions:
            query = f'[out:json][timeout:120][bbox:{bbox}];nwr["military"];out center 5000;'
            elements = await overpass_query(client, query, f"mil/{region_name}")

            if not elements:
                continue

            log.info(f"  [{region_name}] Got {len(elements)} military features")
            inserted = 0

            for el in elements:
                lat = el.get("lat") or el.get("center", {}).get("lat")
                lng = el.get("lon") or el.get("center", {}).get("lon")
                if not lat or not lng:
                    continue

                tags = el.get("tags", {})
                name = tags.get("name", tags.get("military", "Military Installation"))
                mil_type = tags.get("military", "installation")
                source_id = f"osm-{el.get('type', 'node')}-{el.get('id', 0)}"

                try:
                    await conn.execute("""
                        INSERT INTO military.installations (source, source_id, name, installation_type,
                            location, country, properties)
                        VALUES ('osm', $1, $2, $3,
                            ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography,
                            $6, $7::jsonb)
                        ON CONFLICT DO NOTHING
                    """,
                        source_id, name, mil_type,
                        lng, lat, tags.get("addr:country"),
                        json.dumps(tags),
                    )
                    inserted += 1
                except Exception:
                    pass

            log.info(f"  [{region_name}] Inserted {inserted} military installations")
            total += inserted
            await asyncio.sleep(3)

    log.info(f"  TOTAL: {total} military installations loaded")
    return total


async def load_transmission_lines(conn: asyncpg.Connection):
    """Load transmission lines (power=line) from OSM — LINESTRING geometries."""
    log.info("=" * 60)
    log.info("Loading TRANSMISSION LINES from OpenStreetMap")
    log.info("=" * 60)

    total = 0
    async with httpx.AsyncClient() as client:
        # Use smaller regions for transmission lines — they're dense
        tx_regions = [
            ("US-NE", "38,-82,46,-66"),
            ("US-SE", "28,-90,38,-74"),
            ("US-MW", "38,-100,48,-82"),
            ("US-SW", "28,-118,38,-100"),
            ("US-NW", "42,-125,50,-108"),
            ("US-TX", "25,-106,37,-93"),
            ("EU-W", "42,-5,55,10"),
            ("EU-C", "45,10,56,25"),
            ("UK", "50,-6,58,2"),
            ("East-Asia", "30,120,45,145"),
            ("India", "8,68,35,90"),
        ]

        for region_name, bbox in tx_regions:
            # Query transmission lines as ways with geometry
            query = f'[out:json][timeout:120][bbox:{bbox}];way["power"="line"];out geom 5000;'
            elements = await overpass_query(client, query, f"tx/{region_name}")

            if not elements:
                continue

            log.info(f"  [{region_name}] Got {len(elements)} transmission lines")
            inserted = 0

            for el in elements:
                geom = el.get("geometry", [])
                if len(geom) < 2:
                    continue

                tags = el.get("tags", {})
                voltage_str = tags.get("voltage", "0")
                try:
                    voltage_kv = max(float(v) / 1000 for v in voltage_str.replace(";", ",").split(",") if v.strip())
                except (ValueError, TypeError):
                    voltage_kv = 0

                # Build LINESTRING from way geometry
                coord_pairs = []
                for pt in geom:
                    lat = pt.get("lat")
                    lon = pt.get("lon")
                    if lat and lon:
                        coord_pairs.append(f"{lon} {lat}")

                if len(coord_pairs) < 2:
                    continue

                linestring_wkt = f"LINESTRING({', '.join(coord_pairs)})"
                source_id = f"osm-way-{el.get('id', 0)}"
                name = tags.get("name", f"TX Line {voltage_kv:.0f}kV")
                operator = tags.get("operator")

                try:
                    await conn.execute("""
                        INSERT INTO infra.power_grid (source, source_id, asset_type, name, voltage_kv,
                            location, operator, properties)
                        VALUES ('osm', $1, 'transmission_line', $2, $3,
                            ST_GeogFromText($4),
                            $5, $6::jsonb)
                        ON CONFLICT DO NOTHING
                    """,
                        source_id, name, voltage_kv,
                        linestring_wkt, operator,
                        json.dumps({**tags, "voltage_kv": voltage_kv}),
                    )
                    inserted += 1
                except Exception as e:
                    if inserted == 0:
                        log.error(f"  TX line insert error: {e}")

            log.info(f"  [{region_name}] Inserted {inserted} transmission lines")
            total += inserted
            await asyncio.sleep(3)  # Rate limit

    log.info(f"  TOTAL: {total} transmission lines loaded")
    return total


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="MINDEX OSM Power Infrastructure ETL")
    parser.add_argument("--all", action="store_true", help="Load all data types")
    parser.add_argument("--plants", action="store_true")
    parser.add_argument("--substations", action="store_true")
    parser.add_argument("--lines", action="store_true")
    parser.add_argument("--datacenters", action="store_true")
    parser.add_argument("--cables", action="store_true")
    parser.add_argument("--military", action="store_true")
    args = parser.parse_args()

    if not any(vars(args).values()):
        args.all = True

    log.info("=" * 60)
    log.info("MINDEX OSM Power Infrastructure ETL")
    log.info("=" * 60)

    conn = await get_db()
    log.info("Connected to MINDEX database")

    results = {}
    start = time.time()

    try:
        if args.all or args.cables:
            results["submarine_cables"] = await load_submarine_cables(conn)

        if args.all or args.plants:
            results["power_plants"] = await load_power_plants(conn)

        if args.all or args.substations:
            results["substations"] = await load_substations(conn)

        if args.all or args.lines:
            results["transmission_lines"] = await load_transmission_lines(conn)

        if args.all or args.datacenters:
            results["datacenters"] = await load_datacenters(conn)

        if args.all or args.military:
            results["military"] = await load_military_bases(conn)

    finally:
        await conn.close()

    elapsed = time.time() - start
    log.info("=" * 60)
    log.info(f"ETL complete in {elapsed:.1f}s")
    for source, count in results.items():
        log.info(f"  {source}: {count} records")
    total = sum(v for v in results.values() if v)
    log.info(f"  Total: {total} records loaded into MINDEX")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
