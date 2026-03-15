"""
Launch Library 2 / Spaceports / Space Launches
================================================
Space launch data from The Space Devs Launch Library 2 API.
https://thespacedevs.com/llapi

Also covers spaceport locations and launch vehicle data.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

LL2_API = "https://ll.thespacedevs.com/2.2.0"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_launches(
    client: httpx.Client,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Fetch launches from Launch Library 2."""
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    endpoint = "launch/upcoming" if status == "upcoming" else "launch/previous"

    resp = client.get(
        f"{LL2_API}/{endpoint}/",
        params=params,
        timeout=30,
        headers={"User-Agent": "MINDEX-ETL/2.0"},
    )
    resp.raise_for_status()
    return resp.json()


def map_launch(record: dict) -> dict:
    """Map LL2 launch record to MINDEX launch format."""
    pad = record.get("pad", {}) or {}
    pad_loc = pad.get("location", {}) or {}

    return {
        "source": "launch_library",
        "source_id": record.get("id"),
        "name": record.get("name", "Unknown Launch"),
        "provider": record.get("launch_service_provider", {}).get("name"),
        "vehicle": record.get("rocket", {}).get("configuration", {}).get("name"),
        "mission_type": (record.get("mission") or {}).get("type"),
        "pad_name": pad.get("name"),
        "pad_lat": float(pad.get("latitude", 0) or 0),
        "pad_lng": float(pad.get("longitude", 0) or 0),
        "launch_time": record.get("net"),
        "status": record.get("status", {}).get("name"),
        "orbit": (record.get("mission") or {}).get("orbit", {}).get("name") if record.get("mission") else None,
        "properties": {
            "window_start": record.get("window_start"),
            "window_end": record.get("window_end"),
            "webcast_live": record.get("webcast_live"),
            "image": record.get("image"),
            "pad_location": pad_loc.get("name"),
            "country": pad_loc.get("country_code"),
            "mission_description": (record.get("mission") or {}).get("description"),
        },
    }


def iter_launches(
    *,
    status: Optional[str] = None,
    limit: int = 100,
    max_pages: Optional[int] = 5,
    delay_seconds: float = 1.0,
) -> Generator[Dict, None, None]:
    """Iterate through space launches."""
    with httpx.Client() as client:
        offset = 0
        page = 1
        while True:
            data = _fetch_launches(client, status=status, limit=limit, offset=offset)
            results = data.get("results", [])

            if not results:
                break

            for record in results:
                yield map_launch(record)

            if not data.get("next"):
                break

            offset += limit
            page += 1
            if max_pages and page > max_pages:
                break

            time.sleep(delay_seconds)


# ============================================================================
# SPACEPORTS
# ============================================================================

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_spaceports(client: httpx.Client, limit: int = 200) -> list:
    """Fetch spaceport/launch pad data from LL2."""
    resp = client.get(
        f"{LL2_API}/pad/",
        params={"limit": limit},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def map_spaceport(record: dict) -> dict:
    """Map LL2 pad to MINDEX spaceport format."""
    location = record.get("location", {}) or {}
    return {
        "source": "launch_library",
        "name": record.get("name", "Unknown Pad"),
        "operator": record.get("agency_id"),
        "lat": float(record.get("latitude", 0) or 0),
        "lng": float(record.get("longitude", 0) or 0),
        "country": location.get("country_code"),
        "orbital_capable": record.get("orbital_launch_attempt_count", 0) > 0,
        "status": "active" if record.get("total_launch_count", 0) > 0 else "inactive",
        "properties": {
            "total_launches": record.get("total_launch_count"),
            "orbital_launches": record.get("orbital_launch_attempt_count"),
            "map_url": record.get("map_url"),
            "location_name": location.get("name"),
        },
    }


def iter_spaceports() -> Generator[Dict, None, None]:
    """Iterate through spaceport data."""
    with httpx.Client() as client:
        pads = fetch_spaceports(client)
        for pad in pads:
            yield map_spaceport(pad)
