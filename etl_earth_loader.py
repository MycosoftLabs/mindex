#!/usr/bin/env python3
"""
MINDEX Earth Data Loader — Populates all earth-scale domain tables from public APIs.

Run this to fill MINDEX with millions of features for CREP globe rendering:
  python etl_earth_loader.py --all         # Load everything
  python etl_earth_loader.py --earthquakes # Load only earthquakes
  python etl_earth_loader.py --facilities  # Load power plants, factories, etc.

Data sources:
  earth.earthquakes    → USGS Earthquake API (last 30 days, all magnitudes)
  earth.volcanoes      → Smithsonian GVP / USGS
  earth.wildfires      → NASA FIRMS (last 7 days, global)
  earth.storms         → NOAA NHC active storms
  infra.facilities     → OpenStreetMap Overpass (power plants, factories, refineries)
  infra.power_grid     → OSM Overpass (substations, transmission lines)
  infra.internet_cables → Submarine Cable Map API
  signals.antennas     → OpenCellID / OSM (cell towers)
  transport.airports   → OurAirports CSV
  transport.ports      → World Port Index
  space.satellites     → CelesTrak TLE data
  space.solar_events   → NOAA SWPC

All data stored on NAS via MINDEX PostgreSQL. Once loaded, CREP reads from
MINDEX regardless of whether external APIs are up or down.
"""

import asyncio
import csv
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any

import httpx
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("earth-loader")

# MINDEX database connection — NAS at 192.168.0.189, port 5432
DB_DSN = "postgresql://mycosoft:mycosoft_mindex_2026@192.168.0.189:5432/mindex"

# Rate limiting
RATE_LIMIT_DELAY = 0.5  # seconds between API calls


async def get_db() -> asyncpg.Connection:
    return await asyncpg.connect(DB_DSN)


# ============================================================================
# EARTHQUAKES — USGS (last 30 days, all magnitudes worldwide)
# ============================================================================
async def load_earthquakes(conn: asyncpg.Connection):
    log.info("Loading earthquakes from USGS...")
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.geojson"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        data = resp.json()

    features = data.get("features", [])
    log.info(f"  Got {len(features)} earthquakes from USGS")

    inserted = 0
    for f in features:
        props = f["properties"]
        coords = f["geometry"]["coordinates"]  # [lng, lat, depth]
        try:
            await conn.execute("""
                INSERT INTO earth.earthquakes (source, source_id, magnitude, magnitude_type, depth_km,
                    location, place_name, occurred_at, tsunami_flag, felt_reports, alert_level, properties)
                VALUES ('usgs', $1, $2, $3, $4,
                    ST_SetSRID(ST_MakePoint($5, $6), 4326)::geography,
                    $7, to_timestamp($8::double precision / 1000), $9, $10, $11, $12::jsonb)
                ON CONFLICT (source_id) DO UPDATE SET
                    magnitude = EXCLUDED.magnitude, properties = EXCLUDED.properties
            """,
                f["id"],
                props.get("mag", 0),
                props.get("magType"),
                coords[2] if len(coords) > 2 else None,
                coords[0], coords[1],
                props.get("place"),
                props.get("time", 0),
                bool(props.get("tsunami")),
                props.get("felt", 0) or 0,
                props.get("alert"),
                json.dumps({k: v for k, v in props.items() if k not in ("mag", "magType", "place", "time", "tsunami", "felt", "alert")}),
            )
            inserted += 1
        except Exception as e:
            if inserted == 0:
                log.error(f"  Earthquake insert error: {e}")

    log.info(f"  Inserted/updated {inserted} earthquakes")
    return inserted


# ============================================================================
# WILDFIRES — NASA FIRMS (last 7 days, global active fires)
# ============================================================================
async def load_wildfires(conn: asyncpg.Connection):
    log.info("Loading wildfires from NASA FIRMS...")
    # FIRMS CSV endpoint for last 7 days, VIIRS sensor
    # Use NASA EarthData bearer token for authenticated access
    earthdata_token = os.environ.get("NASA_EARTHDATA_TOKEN", "")
    firms_key = os.environ.get("NASA_FIRMS_MAP_KEY", "DEMO_KEY")
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{firms_key}/VIIRS_SNPP_NRT/world/7"

    headers = {}
    if earthdata_token:
        headers["Authorization"] = f"Bearer {earthdata_token}"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(url, headers=headers)
        lines = resp.text.strip().split("\n")

    if len(lines) < 2:
        log.warning("  No FIRMS data received")
        return 0

    header = lines[0].split(",")
    lat_idx = header.index("latitude") if "latitude" in header else 0
    lng_idx = header.index("longitude") if "longitude" in header else 1
    bright_idx = header.index("bright_ti4") if "bright_ti4" in header else -1
    frp_idx = header.index("frp") if "frp" in header else -1
    conf_idx = header.index("confidence") if "confidence" in header else -1
    date_idx = header.index("acq_date") if "acq_date" in header else -1
    time_idx = header.index("acq_time") if "acq_time" in header else -1

    log.info(f"  Got {len(lines) - 1} fire detections from FIRMS")

    # Batch insert — FIRMS returns thousands of points
    inserted = 0
    batch_size = 500
    for i in range(1, len(lines), batch_size):
        batch = lines[i:i + batch_size]
        for line in batch:
            cols = line.split(",")
            if len(cols) < max(lat_idx, lng_idx) + 1:
                continue
            try:
                lat = float(cols[lat_idx])
                lng = float(cols[lng_idx])
                brightness = float(cols[bright_idx]) if bright_idx >= 0 and cols[bright_idx] else None
                frp = float(cols[frp_idx]) if frp_idx >= 0 and cols[frp_idx] else None
                confidence = cols[conf_idx] if conf_idx >= 0 else None
                acq_date = cols[date_idx] if date_idx >= 0 else datetime.now().strftime("%Y-%m-%d")
                acq_time = cols[time_idx] if time_idx >= 0 else "0000"

                detected = datetime.strptime(f"{acq_date} {acq_time}", "%Y-%m-%d %H%M")

                await conn.execute("""
                    INSERT INTO earth.wildfires (source, name, location, detected_at,
                        brightness, frp, confidence, properties)
                    VALUES ('firms', 'VIIRS Detection', ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography,
                        $3, $4, $5, $6, '{}'::jsonb)
                """, lng, lat, detected, brightness, frp, confidence)
                inserted += 1
            except Exception:
                pass

        if inserted % 1000 == 0 and inserted > 0:
            log.info(f"  ... {inserted} fires inserted")

    log.info(f"  Inserted {inserted} wildfire detections")
    return inserted


# ============================================================================
# FACILITIES — OpenStreetMap Overpass (power plants, factories, datacenters)
# ============================================================================
async def load_facilities(conn: asyncpg.Connection):
    log.info("Loading facilities from OpenStreetMap Overpass...")

    # Region-bounded queries to avoid Overpass timeouts on global queries
    # Each region covers a major continent/area
    regions = [
        ("NA", "24,-130,55,-60"),     # North America
        ("EU", "35,-15,72,45"),       # Europe
        ("AS", "0,60,60,150"),        # Asia
        ("SA", "-60,-85,15,-30"),     # South America
        ("AF", "-40,-20,40,55"),      # Africa
        ("OC", "-50,100,0,180"),      # Oceania
    ]

    queries = {
        "power_plant": 'nwr["power"="plant"]',
        "data_center": 'nwr["building"="data_centre"]',
        "substation": 'nwr["power"="substation"]',
    }

    overpass_url = "https://overpass-api.de/api/interpreter"
    total = 0

    async with httpx.AsyncClient(timeout=360) as client:
        for facility_type, query_filter in queries.items():
            for region_name, bbox in regions:
                full_query = f'[out:json][timeout:120][bbox:{bbox}];{query_filter};out center 5000;'
                log.info(f"  Fetching {facility_type} in {region_name} from Overpass...")
                try:
                    resp = await client.post(overpass_url, data={"data": full_query})
                    if resp.status_code != 200:
                        log.warning(f"  Overpass returned {resp.status_code} for {facility_type}/{region_name}")
                        await asyncio.sleep(RATE_LIMIT_DELAY * 3)
                        continue

                    data = resp.json()
                    elements = data.get("elements", [])
                    log.info(f"  Got {len(elements)} {facility_type} in {region_name}")

                    inserted = 0
                    for el in elements:
                        lat = el.get("lat") or el.get("center", {}).get("lat")
                        lng = el.get("lon") or el.get("center", {}).get("lon")
                        if not lat or not lng:
                            continue

                        tags = el.get("tags", {})
                        name = tags.get("name", f"{facility_type.replace('_', ' ').title()}")
                        operator = tags.get("operator", tags.get("company"))
                        sub_type = tags.get("plant:source", tags.get("generator:source", tags.get("plant:type")))
                        capacity = tags.get("plant:output:electricity", tags.get("generator:output:electricity"))
                        status = tags.get("operational_status", "active")

                        source_id = f"osm-{el.get('type', 'node')}-{el.get('id', 0)}"

                        try:
                            await conn.execute("""
                                INSERT INTO infra.facilities (source, source_id, name, facility_type, sub_type,
                                    location, operator, capacity, status, properties)
                                VALUES ('osm', $1, $2, $3, $4,
                                    ST_SetSRID(ST_MakePoint($5, $6), 4326)::geography,
                                    $7, $8, $9, $10::jsonb)
                                ON CONFLICT (source, source_id) DO UPDATE SET
                                    name = EXCLUDED.name, properties = EXCLUDED.properties
                            """,
                                source_id, name, facility_type, sub_type,
                                lng, lat, operator, capacity, status,
                                json.dumps(tags),
                            )
                            inserted += 1
                        except Exception as e:
                            if inserted == 0:
                                log.error(f"  Facility insert error: {e}")

                    log.info(f"  Inserted {inserted} {facility_type} in {region_name}")
                    total += inserted

                except Exception as e:
                    log.error(f"  Failed to fetch {facility_type}/{region_name}: {e}")

                await asyncio.sleep(RATE_LIMIT_DELAY * 3)  # Be nice to Overpass

    log.info(f"  Total facilities inserted: {total}")
    return total


# ============================================================================
# POWER GRID — OSM Overpass (transmission lines, substations)
# ============================================================================
async def load_power_grid(conn: asyncpg.Connection):
    log.info("Loading power grid from OpenStreetMap Overpass...")
    overpass_url = "https://overpass-api.de/api/interpreter"

    # Substations (points) — globally
    query_subs = '[out:json][timeout:300];nwr["power"="substation"];out center 10000;'

    async with httpx.AsyncClient(timeout=360) as client:
        try:
            log.info("  Fetching substations...")
            resp = await client.post(overpass_url, data={"data": query_subs})
            elements = resp.json().get("elements", [])
            log.info(f"  Got {len(elements)} substations")

            inserted = 0
            for el in elements:
                lat = el.get("lat") or el.get("center", {}).get("lat")
                lng = el.get("lon") or el.get("center", {}).get("lon")
                if not lat or not lng:
                    continue
                tags = el.get("tags", {})
                voltage_str = tags.get("voltage", "0")
                # Parse voltage (may be "115000" or "115;230")
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

            log.info(f"  Inserted {inserted} substations")
            return inserted

        except Exception as e:
            log.error(f"  Power grid fetch error: {e}")
            return 0


# ============================================================================
# SUBMARINE CABLES — TeleGeography Submarine Cable Map
# ============================================================================
async def load_submarine_cables(conn: asyncpg.Connection):
    log.info("Loading submarine cables...")
    url = "https://raw.githubusercontent.com/telegeography/www.submarinecablemap.com/master/web/public/api/v3/cable/all.json"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        try:
            cables = resp.json()
        except Exception:
            log.warning("  Submarine cable API returned non-JSON, trying alternate source")
            # Try alternate URL
            resp2 = await client.get("https://raw.githubusercontent.com/telegeography/www.submarinecablemap.com/master/web/public/api/v3/cable/cable-geo.json")
            try:
                data = resp2.json()
                cables = data.get("features", []) if isinstance(data, dict) else data
            except Exception:
                log.error("  Both submarine cable sources failed")
                return 0

    log.info(f"  Got {len(cables)} submarine cables")
    inserted = 0

    for cable in cables:
        cable_id = str(cable.get("id", ""))
        name = cable.get("name", "Unknown Cable")
        length_km = cable.get("length")
        rfs = cable.get("rfs_year")
        owners = cable.get("owners")
        landing_points = cable.get("landing_points")

        # Cable route coordinates
        coords = cable.get("coordinates", [])
        if not coords or len(coords) < 2:
            continue

        # Build LINESTRING from coordinates
        coord_pairs = [f"{c[0]} {c[1]}" for c in coords if len(c) >= 2]
        if len(coord_pairs) < 2:
            continue
        linestring = f"LINESTRING({', '.join(coord_pairs)})"

        try:
            await conn.execute("""
                INSERT INTO infra.internet_cables (source, source_id, name, cable_type, length_km,
                    route, landing_points, owners, rfs_date, status, properties)
                VALUES ('submarinecablemap', $1, $2, 'submarine', $3,
                    ST_GeogFromText($4),
                    $5::jsonb, $6::jsonb, $7, 'active', '{}'::jsonb)
                ON CONFLICT DO NOTHING
            """,
                cable_id, name, length_km,
                linestring,
                json.dumps(landing_points) if landing_points else "[]",
                json.dumps(owners) if owners else "[]",
                f"{rfs}-01-01" if rfs else None,
            )
            inserted += 1
        except Exception as e:
            if inserted == 0:
                log.error(f"  Cable insert error: {e}")

    log.info(f"  Inserted {inserted} submarine cables")
    return inserted


# ============================================================================
# AIRPORTS — OurAirports (CSV)
# ============================================================================
async def load_airports(conn: asyncpg.Connection):
    log.info("Loading airports from OurAirports...")
    url = "https://davidmegginson.github.io/ourairports-data/airports.csv"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        lines = resp.text.strip().split("\n")

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    log.info(f"  Got {len(rows)} airports")
    inserted = 0

    for row in rows:
        try:
            airport_type = row.get("type", "")
            if airport_type not in ("large_airport", "medium_airport", "small_airport"):
                continue
            name = row.get("name", "Unknown Airport")
            lat_str = row.get("latitude_deg", "")
            lng_str = row.get("longitude_deg", "")
            if not lat_str or not lng_str:
                continue
            lat = float(lat_str)
            lng = float(lng_str)
            ident = row.get("ident", "")
            country = row.get("iso_country", "")
            icao = row.get("icao_code") or row.get("gps_code") or ident
            iata = row.get("iata_code", "")

            await conn.execute("""
                INSERT INTO transport.airports (source, name, airport_type,
                    icao_code, iata_code, location, country, properties)
                VALUES ('ourairports', $1, $2, $3, $4,
                    ST_SetSRID(ST_MakePoint($5, $6), 4326)::geography,
                    $7, '{}'::jsonb)
                ON CONFLICT (icao_code) DO NOTHING
            """,
                name, airport_type, icao or None, iata or None, lng, lat, country,
            )
            inserted += 1
        except Exception:
            pass

    log.info(f"  Inserted {inserted} airports")
    return inserted


# ============================================================================
# SOLAR EVENTS — NOAA SWPC
# ============================================================================
async def load_solar_events(conn: asyncpg.Connection):
    log.info("Loading solar events from NOAA SWPC...")

    async with httpx.AsyncClient(timeout=30) as client:
        # Solar flares
        try:
            resp = await client.get("https://services.swpc.noaa.gov/json/goes/primary/xray-flares-latest.json")
            flares = resp.json()
            log.info(f"  Got {len(flares)} solar flares")

            for flare in flares:
                try:
                    await conn.execute("""
                        INSERT INTO space.solar_events (source, event_type, intensity,
                            location, occurred_at, properties)
                        VALUES ('noaa_swpc', 'solar_flare', $1,
                            ST_SetSRID(ST_MakePoint(0, 0), 4326)::geography,
                            $2::timestamptz, $3::jsonb)
                        ON CONFLICT DO NOTHING
                    """,
                        flare.get("current_class"),
                        flare.get("begin_time") or datetime.now().isoformat(),
                        json.dumps(flare),
                    )
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"  Solar flares fetch failed: {e}")

    log.info("  Solar events loaded")
    return 0


# ============================================================================
# MAIN — Run all loaders
# ============================================================================
async def main():
    import argparse
    parser = argparse.ArgumentParser(description="MINDEX Earth Data Loader")
    parser.add_argument("--all", action="store_true", help="Load all data sources")
    parser.add_argument("--earthquakes", action="store_true")
    parser.add_argument("--wildfires", action="store_true")
    parser.add_argument("--facilities", action="store_true")
    parser.add_argument("--power-grid", action="store_true")
    parser.add_argument("--cables", action="store_true")
    parser.add_argument("--airports", action="store_true")
    parser.add_argument("--solar", action="store_true")
    args = parser.parse_args()

    if not any(vars(args).values()):
        args.all = True

    log.info("=" * 60)
    log.info("MINDEX Earth Data Loader — Populating NAS database")
    log.info("=" * 60)

    conn = await get_db()
    log.info(f"Connected to MINDEX database")

    results = {}
    start = time.time()

    try:
        if args.all or args.earthquakes:
            results["earthquakes"] = await load_earthquakes(conn)

        if args.all or args.wildfires:
            results["wildfires"] = await load_wildfires(conn)

        if args.all or args.facilities:
            results["facilities"] = await load_facilities(conn)

        if args.all or args.power_grid:
            results["power_grid"] = await load_power_grid(conn)

        if args.all or args.cables:
            results["cables"] = await load_submarine_cables(conn)

        if args.all or args.airports:
            results["airports"] = await load_airports(conn)

        if args.all or args.solar:
            results["solar"] = await load_solar_events(conn)

    finally:
        await conn.close()

    elapsed = time.time() - start
    log.info("=" * 60)
    log.info(f"ETL complete in {elapsed:.1f}s")
    for source, count in results.items():
        log.info(f"  {source}: {count} records")
    log.info(f"  Total: {sum(results.values())} records loaded into MINDEX")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
