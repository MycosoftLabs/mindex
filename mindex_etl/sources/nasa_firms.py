"""
NASA FIRMS (Fire Information for Resource Management System)
============================================================
Active fire/hotspot data from MODIS and VIIRS satellite instruments.
https://firms.modaps.eosdis.nasa.gov/api/

Also covers wildfire tracking from NIFC and InciWeb.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

logger = logging.getLogger(__name__)

FIRMS_API = "https://firms.modaps.eosdis.nasa.gov/api"
# Suomi NPP FIRMS delivery ends 2026-11-01; NOAA-20 and NOAA-21 carry VIIRS NRT.
FIRMS_SOURCES = ("VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
# The MAP_KEY is shared by every Mycosoft system (5000 transactions / 10 min),
# so identical requests are served from memory for 10 minutes.
FIRMS_CACHE_TTL_SECONDS = 600
_firms_cache: Dict[tuple, tuple] = {}


def _firms_map_key() -> str:
    return (settings.nasa_firms_map_key or "").strip()


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_firms_data(
    client: httpx.Client,
    source: str = FIRMS_SOURCES[0],
    area: str = "world",
    days: int = 1,
) -> list:
    """Fetch active fire data from FIRMS."""
    url = f"{FIRMS_API}/area/csv/{_firms_map_key()}/{source}/{area}/{days}"
    resp = client.get(url, timeout=120, headers={
        "User-Agent": "MINDEX-ETL/2.0 (Mycosoft Earth Data Platform)",
    })
    resp.raise_for_status()

    lines = resp.text.strip().split("\n")
    if len(lines) < 2:
        return []

    headers = lines[0].split(",")
    results = []
    for line in lines[1:]:
        values = line.split(",")
        if len(values) == len(headers):
            results.append(dict(zip(headers, values)))
    return results


def map_fire_hotspot(record: dict) -> dict:
    """Map FIRMS CSV record to MINDEX wildfire format."""
    return {
        "source": "firms",
        "source_id": (
            f"firms_{record.get('satellite', '')}_{record.get('latitude')}_{record.get('longitude')}"
            f"_{record.get('acq_date')}_{record.get('acq_time', '0000')}"
        ),
        "name": None,
        "lat": float(record.get("latitude", 0)),
        "lng": float(record.get("longitude", 0)),
        "detected_at": f"{record.get('acq_date')} {record.get('acq_time', '0000')}",
        "brightness": float(record.get("bright_ti4", 0) or record.get("brightness", 0)),
        "frp": float(record.get("frp", 0) or 0),
        "confidence": record.get("confidence"),
        "status": "active",
        "properties": {
            "satellite": record.get("satellite"),
            "instrument": record.get("instrument"),
            "scan": record.get("scan"),
            "track": record.get("track"),
            "version": record.get("version"),
            "daynight": record.get("daynight"),
        },
    }


def iter_fire_hotspots(
    *,
    sources: tuple = FIRMS_SOURCES,
    area: str = "world",
    days: int = 1,
) -> Generator[Dict, None, None]:
    """Iterate through FIRMS fire hotspot data."""
    if not _firms_map_key():
        logger.warning("NASA_FIRMS_MAP_KEY not set; skipping FIRMS hotspots")
        return
    with httpx.Client() as client:
        for source in sources:
            cache_key = (source, area, days)
            cached = _firms_cache.get(cache_key)
            try:
                if cached and time.monotonic() - cached[0] < FIRMS_CACHE_TTL_SECONDS:
                    records = cached[1]
                else:
                    records = _fetch_firms_data(client, source, area, days)
                    _firms_cache[cache_key] = (time.monotonic(), records)
            except Exception as e:
                logger.warning("FIRMS %s fetch failed: %s", source, e)
                continue
            logger.info("FIRMS %s: %d hotspots", source, len(records))
            for record in records:
                yield map_fire_hotspot(record)


NIFC_WFIGS_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Incident_Locations_Current/FeatureServer/0/query"
)


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_nifc_wildfires(client: httpx.Client) -> list:
    """Fetch current wildfire incidents from NIFC WFIGS (public layer, no token)."""
    params = {
        "where": "1=1",
        "outFields": "OBJECTID,UniqueFireIdentifier,IncidentName,IncidentSize,PercentContained,"
                     "FireDiscoveryDateTime,POOState,POOCounty,FireCause,IncidentTypeCategory",
        "outSR": 4326,
        "f": "json",
        "resultRecordCount": 2000,
    }
    resp = client.get(url=NIFC_WFIGS_URL, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"NIFC WFIGS error: {data['error']}")
    return data.get("features", [])


def _epoch_ms_to_iso(value: Any) -> Optional[str]:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    return value


def map_nifc_wildfire(feature: dict) -> dict:
    """Map NIFC WFIGS feature to MINDEX wildfire format."""
    attrs = feature.get("attributes", {})
    geom = feature.get("geometry", {})
    fire_id = attrs.get("UniqueFireIdentifier") or attrs.get("OBJECTID")
    return {
        "source": "nifc",
        "source_id": f"nifc_{fire_id}",
        "name": attrs.get("IncidentName"),
        "lat": geom.get("y"),
        "lng": geom.get("x"),
        "area_acres": attrs.get("IncidentSize"),
        "containment_pct": attrs.get("PercentContained"),
        "status": "active",
        "detected_at": _epoch_ms_to_iso(attrs.get("FireDiscoveryDateTime")),
        "brightness": None,
        "frp": None,
        "confidence": None,
        "properties": {
            "fire_cause": attrs.get("FireCause"),
            "incident_type": attrs.get("IncidentTypeCategory"),
            "state": attrs.get("POOState"),
            "county": attrs.get("POOCounty"),
        },
    }


def iter_active_wildfires() -> Generator[Dict, None, None]:
    """Iterate through active wildfires from multiple sources."""
    with httpx.Client() as client:
        # NIFC wildfires
        try:
            features = fetch_nifc_wildfires(client)
        except Exception as e:
            logger.warning("NIFC WFIGS fetch failed: %s", e)
            features = []
        for f in features:
            yield map_nifc_wildfire(f)

    # FIRMS hotspots
    yield from iter_fire_hotspots(days=1)
