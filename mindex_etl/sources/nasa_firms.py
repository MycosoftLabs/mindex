"""
NASA FIRMS (Fire Information for Resource Management System)
============================================================
Active fire/hotspot data from MODIS and VIIRS satellite instruments.
https://firms.modaps.eosdis.nasa.gov/api/

Also covers wildfire tracking from NIFC and InciWeb.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

FIRMS_API = "https://firms.modaps.eosdis.nasa.gov/api"
FIRMS_MAP_KEY = getattr(settings, "nasa_firms_map_key", "")


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_firms_data(
    client: httpx.Client,
    source: str = "VIIRS_SNPP_NRT",
    area: str = "world",
    days: int = 1,
) -> list:
    """Fetch active fire data from FIRMS."""
    url = f"{FIRMS_API}/area/csv/{FIRMS_MAP_KEY}/{source}/{area}/{days}"
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
        "source_id": f"firms_{record.get('latitude')}_{record.get('longitude')}_{record.get('acq_date')}",
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
    source: str = "VIIRS_SNPP_NRT",
    area: str = "world",
    days: int = 1,
) -> Generator[Dict, None, None]:
    """Iterate through FIRMS fire hotspot data."""
    with httpx.Client() as client:
        records = _fetch_firms_data(client, source, area, days)
        for record in records:
            yield map_fire_hotspot(record)


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_nifc_wildfires(client: httpx.Client) -> list:
    """Fetch active wildfires from NIFC (National Interagency Fire Center)."""
    url = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/Active_Fires/FeatureServer/0/query"
    params = {
        "where": "1=1",
        "outFields": "*",
        "f": "json",
        "resultRecordCount": 1000,
    }
    resp = client.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json().get("features", [])


def map_nifc_wildfire(feature: dict) -> dict:
    """Map NIFC ArcGIS feature to MINDEX wildfire format."""
    attrs = feature.get("attributes", {})
    geom = feature.get("geometry", {})
    return {
        "source": "nifc",
        "source_id": str(attrs.get("OBJECTID") or attrs.get("UniqueFireIdentifier")),
        "name": attrs.get("IncidentName"),
        "lat": geom.get("y"),
        "lng": geom.get("x"),
        "area_acres": attrs.get("DailyAcres") or attrs.get("GISAcres"),
        "containment_pct": attrs.get("PercentContained"),
        "status": "active",
        "detected_at": attrs.get("FireDiscoveryDateTime"),
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
            for f in features:
                yield map_nifc_wildfire(f)
        except Exception:
            pass

    # FIRMS hotspots
    yield from iter_fire_hotspots(days=1)
