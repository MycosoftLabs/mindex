"""
Earth Data Sync Orchestrator
==============================
Master job that coordinates all planetary data ingestion into local MINDEX DB.

Manages two sync tiers:
1. REAL-TIME (every 15 min): earthquakes, wildfires, aircraft, vessels, weather,
   air quality, solar events, NWS alerts
2. CATALOG (daily): satellites, airports, ports, spaceports, facilities, dams,
   submarine cables, cell towers, species

All data is scraped from external APIs and stored locally in PostgreSQL
for instant low-latency access. This data feeds:
- Unified Earth Search API
- CREP map rendering
- Myca knowledge base
- Nature Learning Model training
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from ..config import settings

logger = logging.getLogger(__name__)


def _get_conn():
    """Get synchronous DB connection for ETL jobs."""
    return psycopg2.connect(settings.database_url)


def _upsert_batch(conn, table: str, records: List[dict], conflict_col: str = "source_id"):
    """Generic upsert for ETL records with PostGIS geometry."""
    if not records:
        return 0

    cur = conn.cursor()
    count = 0

    for record in records:
        try:
            # Build column list from record keys
            cols = list(record.keys())
            # Handle lat/lng -> location geography conversion
            if "lat" in record and "lng" in record and "location" not in cols:
                lat = record.pop("lat")
                lng = record.pop("lng")
                cols = list(record.keys())
                cols.append("location")
                placeholders = [f"%({c})s" for c in record.keys()]
                placeholders.append(f"ST_MakePoint({lng}, {lat})::geography")
            else:
                placeholders = [f"%({c})s" for c in cols]

            col_str = ", ".join(cols)
            val_str = ", ".join(placeholders)

            sql = f"""
                INSERT INTO {table} ({col_str})
                VALUES ({val_str})
                ON CONFLICT ({conflict_col}) DO UPDATE SET
                    {', '.join(f'{c} = EXCLUDED.{c}' for c in cols if c != conflict_col and c != 'id')}
            """
            cur.execute(sql, record)
            count += 1
        except Exception as e:
            logger.debug(f"Upsert error for {table}: {e}")
            conn.rollback()
            continue

    conn.commit()
    cur.close()
    return count


# ============================================================================
# REAL-TIME SYNC JOBS
# ============================================================================

def sync_earthquakes(hours: int = 24, min_magnitude: float = 2.5):
    """Sync recent earthquakes from USGS."""
    from ..sources.usgs_earthquakes import fetch_recent_earthquakes

    logger.info(f"Syncing earthquakes (last {hours}h, M>={min_magnitude})")
    records = fetch_recent_earthquakes(hours=hours, min_magnitude=min_magnitude)

    conn = _get_conn()
    cur = conn.cursor()
    count = 0

    for record in records:
        try:
            occurred_ms = record.get("occurred_at")
            if isinstance(occurred_ms, (int, float)):
                occurred_at = datetime.fromtimestamp(occurred_ms / 1000, tz=timezone.utc)
            else:
                occurred_at = occurred_ms

            cur.execute("""
                INSERT INTO earth.earthquakes (source, source_id, magnitude, magnitude_type,
                    depth_km, location, place_name, occurred_at, tsunami_flag, felt_reports,
                    alert_level, properties)
                VALUES (%(source)s, %(source_id)s, %(magnitude)s, %(magnitude_type)s,
                    %(depth_km)s, ST_MakePoint(%(lng)s, %(lat)s)::geography,
                    %(place_name)s, %(occurred_at)s, %(tsunami_flag)s, %(felt_reports)s,
                    %(alert_level)s, %(properties)s::jsonb)
                ON CONFLICT (source_id) DO UPDATE SET
                    magnitude = EXCLUDED.magnitude,
                    alert_level = EXCLUDED.alert_level,
                    felt_reports = EXCLUDED.felt_reports,
                    properties = EXCLUDED.properties
            """, {
                **record,
                "occurred_at": occurred_at,
                "properties": psycopg2.extras.Json(record.get("properties", {})),
            })
            count += 1
        except Exception as e:
            logger.debug(f"Earthquake upsert error: {e}")
            conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Synced {count} earthquakes")
    return count


def sync_wildfires():
    """Sync active wildfires from FIRMS + NIFC."""
    from ..sources.nasa_firms import iter_active_wildfires

    logger.info("Syncing active wildfires")
    conn = _get_conn()
    cur = conn.cursor()
    count = 0

    for record in iter_active_wildfires():
        try:
            cur.execute("""
                INSERT INTO earth.wildfires (source, source_id, name, location,
                    area_acres, containment_pct, status, detected_at, brightness, frp,
                    confidence, properties)
                VALUES (%(source)s, %(source_id)s, %(name)s,
                    ST_MakePoint(%(lng)s, %(lat)s)::geography,
                    %(area_acres)s, %(containment_pct)s, %(status)s, %(detected_at)s,
                    %(brightness)s, %(frp)s, %(confidence)s, %(properties)s::jsonb)
                ON CONFLICT (source_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    area_acres = EXCLUDED.area_acres,
                    containment_pct = EXCLUDED.containment_pct,
                    brightness = EXCLUDED.brightness,
                    frp = EXCLUDED.frp
            """, {
                **record,
                "area_acres": record.get("area_acres"),
                "containment_pct": record.get("containment_pct"),
                "brightness": record.get("brightness"),
                "frp": record.get("frp"),
                "properties": psycopg2.extras.Json(record.get("properties", {})),
            })
            count += 1
        except Exception as e:
            logger.debug(f"Wildfire upsert error: {e}")
            conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Synced {count} wildfire hotspots")
    return count


def sync_solar_events():
    """Sync space weather events from NASA DONKI."""
    from ..sources.noaa import iter_solar_events

    logger.info("Syncing solar/space weather events")
    conn = _get_conn()
    cur = conn.cursor()
    count = 0

    for record in iter_solar_events():
        try:
            cur.execute("""
                INSERT INTO space.solar_events (source, event_type, class, intensity,
                    kp_index, speed_km_s, source_region, start_time, peak_time, end_time,
                    earth_directed, properties)
                VALUES (%(source)s, %(event_type)s, %(class)s, %(intensity)s,
                    %(kp_index)s, %(speed_km_s)s, %(source_region)s, %(start_time)s,
                    %(peak_time)s, %(end_time)s, %(earth_directed)s, %(properties)s::jsonb)
                ON CONFLICT DO NOTHING
            """, {
                **record,
                "class": record.get("class"),
                "peak_time": record.get("peak_time"),
                "end_time": record.get("end_time"),
                "properties": psycopg2.extras.Json(record.get("properties", {})),
            })
            count += 1
        except Exception as e:
            logger.debug(f"Solar event upsert error: {e}")
            conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Synced {count} solar events")
    return count


def sync_air_quality(country: Optional[str] = None):
    """Sync air quality data from OpenAQ."""
    from ..sources.openaq import iter_air_quality

    logger.info(f"Syncing air quality data (country={country})")
    conn = _get_conn()
    cur = conn.cursor()
    count = 0

    for record in iter_air_quality(country=country, max_pages=3):
        try:
            if record.get("lat") and record.get("lng"):
                cur.execute("""
                    INSERT INTO atmos.air_quality (source, source_id, station_name, location,
                        parameter, value, unit, measured_at, properties)
                    VALUES (%(source)s, %(source_id)s, %(station_name)s,
                        ST_MakePoint(%(lng)s, %(lat)s)::geography,
                        %(parameter)s, %(value)s, %(unit)s, %(measured_at)s, %(properties)s::jsonb)
                    ON CONFLICT DO NOTHING
                """, {
                    **record,
                    "properties": psycopg2.extras.Json(record.get("properties", {})),
                })
                count += 1
        except Exception as e:
            logger.debug(f"Air quality upsert error: {e}")
            conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Synced {count} air quality measurements")
    return count


# ============================================================================
# CATALOG SYNC JOBS (daily)
# ============================================================================

def sync_satellites(groups: Optional[List[str]] = None):
    """Sync satellite catalog from CelesTrak."""
    from ..sources.celestrak import iter_satellites

    logger.info("Syncing satellite catalog from CelesTrak")
    conn = _get_conn()
    cur = conn.cursor()
    count = 0

    for record in iter_satellites(groups=groups or ["active"]):
        try:
            cur.execute("""
                INSERT INTO space.satellites (source, norad_id, cospar_id, name,
                    satellite_type, operator, launch_date, orbit_type, perigee_km,
                    apogee_km, inclination_deg, period_min, tle_line1, tle_line2,
                    tle_epoch, status, properties)
                VALUES (%(source)s, %(norad_id)s, %(cospar_id)s, %(name)s,
                    %(satellite_type)s, %(operator)s, %(launch_date)s, %(orbit_type)s,
                    %(perigee_km)s, %(apogee_km)s, %(inclination_deg)s, %(period_min)s,
                    %(tle_line1)s, %(tle_line2)s, %(tle_epoch)s, %(status)s, %(properties)s::jsonb)
                ON CONFLICT (norad_id) DO UPDATE SET
                    tle_line1 = EXCLUDED.tle_line1,
                    tle_line2 = EXCLUDED.tle_line2,
                    tle_epoch = EXCLUDED.tle_epoch,
                    status = EXCLUDED.status,
                    properties = EXCLUDED.properties
            """, {
                **record,
                "properties": psycopg2.extras.Json(record.get("properties", {})),
            })
            count += 1
        except Exception as e:
            logger.debug(f"Satellite upsert error: {e}")
            conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Synced {count} satellites")
    return count


def sync_launches():
    """Sync space launch data from Launch Library 2."""
    from ..sources.launch_library import iter_launches

    logger.info("Syncing space launches")
    conn = _get_conn()
    cur = conn.cursor()
    count = 0

    for status in ["upcoming", "previous"]:
        for record in iter_launches(status=status, max_pages=3):
            try:
                cur.execute("""
                    INSERT INTO transport.launches (source, source_id, name, provider,
                        vehicle, mission_type, pad_name, pad_location, launch_time,
                        status, orbit, properties)
                    VALUES (%(source)s, %(source_id)s, %(name)s, %(provider)s,
                        %(vehicle)s, %(mission_type)s, %(pad_name)s,
                        ST_MakePoint(%(pad_lng)s, %(pad_lat)s)::geography,
                        %(launch_time)s, %(status)s, %(orbit)s, %(properties)s::jsonb)
                    ON CONFLICT (source_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        launch_time = EXCLUDED.launch_time,
                        properties = EXCLUDED.properties
                """, {
                    **record,
                    "properties": psycopg2.extras.Json(record.get("properties", {})),
                })
                count += 1
            except Exception as e:
                logger.debug(f"Launch upsert error: {e}")
                conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Synced {count} launches")
    return count


def sync_submarine_cables():
    """Sync submarine cable data from TeleGeography."""
    from ..sources.infrastructure import iter_submarine_cables

    logger.info("Syncing submarine cables")
    conn = _get_conn()
    cur = conn.cursor()
    count = 0

    for record in iter_submarine_cables():
        try:
            cur.execute("""
                INSERT INTO infra.internet_cables (source, source_id, name, cable_type,
                    length_km, status, owners, landing_points, rfs_date, properties)
                VALUES (%(source)s, %(source_id)s, %(name)s, %(cable_type)s,
                    %(length_km)s, %(status)s, %(owners)s::jsonb, %(landing_points)s::jsonb,
                    %(rfs_date)s, %(properties)s::jsonb)
                ON CONFLICT (source_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    properties = EXCLUDED.properties
            """, {
                **record,
                "length_km": float(record["length_km"]) if record.get("length_km") else None,
                "owners": psycopg2.extras.Json(record.get("owners")),
                "landing_points": psycopg2.extras.Json(record.get("landing_points")),
                "properties": psycopg2.extras.Json(record.get("properties", {})),
            })
            count += 1
        except Exception as e:
            logger.debug(f"Cable upsert error: {e}")
            conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Synced {count} submarine cables")
    return count


def sync_species_all_kingdoms(max_pages: int = 10):
    """Sync all-kingdom species data from GBIF + iNaturalist (set domain_mode='all')."""
    from ..sources.gbif import iter_gbif_species

    logger.info("Syncing all-kingdom species from GBIF")
    conn = _get_conn()
    cur = conn.cursor()
    count = 0

    for record in iter_gbif_species(domain_mode="all", max_pages=max_pages):
        try:
            metadata = record.get("metadata", {})
            cur.execute("""
                INSERT INTO species.organisms (source, source_id, kingdom, phylum,
                    class_name, order_name, family, genus, scientific_name, common_name,
                    rank, properties)
                VALUES ('gbif', %(gbif_key)s, %(kingdom)s, %(phylum)s,
                    %(class)s, %(order)s, %(family)s, %(genus)s,
                    %(canonical_name)s, %(common_name)s, %(rank)s, %(properties)s::jsonb)
                ON CONFLICT (source, source_id) DO UPDATE SET
                    common_name = EXCLUDED.common_name,
                    properties = EXCLUDED.properties
            """, {
                "gbif_key": str(metadata.get("gbif_key", "")),
                "kingdom": metadata.get("kingdom"),
                "phylum": metadata.get("phylum"),
                "class": metadata.get("class"),
                "order": metadata.get("order"),
                "family": metadata.get("family"),
                "genus": metadata.get("genus"),
                "canonical_name": record.get("canonical_name"),
                "common_name": record.get("common_name"),
                "rank": record.get("rank"),
                "properties": psycopg2.extras.Json(metadata),
            })
            count += 1
        except Exception as e:
            logger.debug(f"Species upsert error: {e}")
            conn.rollback()

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Synced {count} species across all kingdoms")
    return count


# ============================================================================
# MASTER ORCHESTRATOR
# ============================================================================

def run_realtime_sync():
    """Run all real-time data syncs (every 15 min)."""
    logger.info("=" * 60)
    logger.info("EARTH DATA REAL-TIME SYNC STARTING")
    logger.info("=" * 60)

    results = {}
    start = time.time()

    # Real-time feeds
    jobs = [
        ("earthquakes", lambda: sync_earthquakes(hours=1)),
        ("wildfires", sync_wildfires),
        ("solar_events", sync_solar_events),
        ("air_quality", lambda: sync_air_quality()),
    ]

    for name, job in jobs:
        try:
            results[name] = job()
        except Exception as e:
            logger.error(f"Real-time sync failed for {name}: {e}")
            results[name] = f"ERROR: {e}"

    elapsed = round(time.time() - start, 1)
    logger.info(f"Real-time sync complete in {elapsed}s: {results}")
    return results


def run_catalog_sync():
    """Run full catalog syncs (daily)."""
    logger.info("=" * 60)
    logger.info("EARTH DATA CATALOG SYNC STARTING")
    logger.info("=" * 60)

    results = {}
    start = time.time()

    jobs = [
        ("satellites", lambda: sync_satellites(groups=["active", "weather", "gps-ops", "earth-resources"])),
        ("launches", sync_launches),
        ("submarine_cables", sync_submarine_cables),
        ("species_all", lambda: sync_species_all_kingdoms(max_pages=50)),
    ]

    for name, job in jobs:
        try:
            results[name] = job()
        except Exception as e:
            logger.error(f"Catalog sync failed for {name}: {e}")
            results[name] = f"ERROR: {e}"

    elapsed = round(time.time() - start, 1)
    logger.info(f"Catalog sync complete in {elapsed}s: {results}")
    return results


def run_full_earth_sync():
    """Run everything — real-time + catalog. Use for initial data load."""
    logger.info("FULL EARTH DATA SYNC — ALL DOMAINS")
    rt = run_realtime_sync()
    cat = run_catalog_sync()
    return {"realtime": rt, "catalog": cat}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_full_earth_sync()
