"""
USGS Earthquake Hazards Program Data Source
============================================
Real-time and historical earthquake data from the USGS Earthquake API.
https://earthquake.usgs.gov/fdsnws/event/1/

Supports configurable time windows and magnitude filters.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

USGS_API = "https://earthquake.usgs.gov/fdsnws/event/1"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_earthquakes(
    client: httpx.Client,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    min_magnitude: float = 2.5,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    """Fetch earthquake events from USGS API."""
    params: Dict[str, Any] = {
        "format": "geojson",
        "minmagnitude": min_magnitude,
        "limit": limit,
        "offset": offset,
        "orderby": "time",
    }
    if start_time:
        params["starttime"] = start_time
    if end_time:
        params["endtime"] = end_time

    resp = client.get(
        f"{USGS_API}/query",
        params=params,
        timeout=60,
        headers={"User-Agent": "MINDEX-ETL/2.0 (Mycosoft Earth Data Platform)"},
    )
    resp.raise_for_status()
    return resp.json()


def map_earthquake(feature: dict) -> dict:
    """Map GeoJSON feature to MINDEX earthquake format."""
    props = feature.get("properties", {})
    coords = feature.get("geometry", {}).get("coordinates", [0, 0, 0])

    return {
        "source": "usgs",
        "source_id": feature.get("id"),
        "magnitude": props.get("mag"),
        "magnitude_type": props.get("magType"),
        "depth_km": coords[2] if len(coords) > 2 else None,
        "lng": coords[0],
        "lat": coords[1],
        "place_name": props.get("place"),
        "occurred_at": props.get("time"),  # epoch ms
        "tsunami_flag": bool(props.get("tsunami")),
        "felt_reports": props.get("felt") or 0,
        "alert_level": props.get("alert"),
        "properties": {
            "cdi": props.get("cdi"),
            "mmi": props.get("mmi"),
            "status": props.get("status"),
            "net": props.get("net"),
            "code": props.get("code"),
            "gap": props.get("gap"),
            "dmin": props.get("dmin"),
            "rms": props.get("rms"),
            "url": props.get("url"),
            "detail_url": props.get("detail"),
            "types": props.get("types"),
        },
    }


def iter_earthquakes(
    *,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    min_magnitude: float = 2.5,
    limit: int = 500,
    max_pages: Optional[int] = None,
    delay_seconds: float = 0.5,
) -> Generator[Dict, None, None]:
    """Iterate through USGS earthquake data."""
    with httpx.Client() as client:
        offset = 0
        page = 1
        while True:
            payload = _fetch_earthquakes(
                client, start_time, end_time, min_magnitude, limit, offset,
            )
            features = payload.get("features", [])

            if not features:
                break

            for feature in features:
                yield map_earthquake(feature)

            offset += limit
            page += 1
            if max_pages and page > max_pages:
                break

            # Check if we've exhausted results
            metadata = payload.get("metadata", {})
            if offset >= metadata.get("count", 0):
                break

            time.sleep(delay_seconds)


def fetch_recent_earthquakes(hours: int = 24, min_magnitude: float = 2.5) -> list:
    """Convenience: fetch earthquakes from the last N hours."""
    # USGS has pre-built feeds for common time windows
    feed_map = {
        1: "all_hour",
        24: "all_day",
        168: "all_week",
        720: "all_month",
    }

    # Find closest pre-built feed
    feed_key = None
    for h, key in sorted(feed_map.items()):
        if hours <= h:
            feed_key = key
            break

    if feed_key:
        url = f"https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed_key}.geojson"
        with httpx.Client() as client:
            resp = client.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return [
                map_earthquake(f) for f in data.get("features", [])
                if (f.get("properties", {}).get("mag") or 0) >= min_magnitude
            ]

    return list(iter_earthquakes(min_magnitude=min_magnitude, max_pages=5))
