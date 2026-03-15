"""
OpenSky Network — Live Aircraft Tracking
==========================================
ADS-B aircraft position data from the OpenSky Network.
https://openskynetwork.github.io/opensky-api/

Provides real-time and historical aircraft state vectors.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

OPENSKY_API = "https://opensky-network.org/api"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_all_state_vectors(
    client: httpx.Client,
    bbox: Optional[Dict[str, float]] = None,
) -> dict:
    """Fetch all current aircraft state vectors (or within bounding box)."""
    params = {}
    if bbox:
        params.update({
            "lamin": bbox.get("lat_min"),
            "lomin": bbox.get("lng_min"),
            "lamax": bbox.get("lat_max"),
            "lomax": bbox.get("lng_max"),
        })

    resp = client.get(
        f"{OPENSKY_API}/states/all",
        params=params,
        timeout=30,
        headers={"User-Agent": "MINDEX-ETL/2.0"},
    )
    resp.raise_for_status()
    return resp.json()


def map_state_vector(state: list, timestamp: int) -> dict:
    """Map OpenSky state vector array to MINDEX aircraft format.

    State vector indices:
    0: icao24, 1: callsign, 2: origin_country, 3: time_position,
    4: last_contact, 5: longitude, 6: latitude, 7: baro_altitude,
    8: on_ground, 9: velocity, 10: true_track, 11: vertical_rate,
    12: sensors, 13: geo_altitude, 14: squawk, 15: spi, 16: position_source
    """
    return {
        "source": "opensky",
        "icao24": state[0],
        "callsign": (state[1] or "").strip(),
        "registration": None,
        "aircraft_type": None,
        "origin": None,
        "destination": None,
        "lat": state[6],
        "lng": state[5],
        "altitude_ft": round(state[7] * 3.28084, 0) if state[7] else None,
        "ground_speed_kts": round(state[9] * 1.94384, 1) if state[9] else None,
        "heading": state[10],
        "vertical_rate": state[11],
        "on_ground": state[8],
        "squawk": state[14],
        "observed_at": timestamp,
        "properties": {
            "origin_country": state[2],
            "geo_altitude_m": state[13],
            "position_source": state[16],
        },
    }


def iter_aircraft(
    bbox: Optional[Dict[str, float]] = None,
) -> Generator[Dict, None, None]:
    """Iterate through current aircraft positions."""
    with httpx.Client() as client:
        data = fetch_all_state_vectors(client, bbox)
        timestamp = data.get("time", int(time.time()))
        states = data.get("states", [])

        for state in states:
            if state[5] is not None and state[6] is not None:  # has position
                yield map_state_vector(state, timestamp)


# ============================================================================
# ADS-B Exchange (fallback / extended data)
# ============================================================================

ADSB_EXCHANGE_API = "https://adsbexchange.com/api/aircraft/v2"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_adsb_exchange_nearby(
    client: httpx.Client,
    lat: float,
    lng: float,
    radius_nm: int = 250,
) -> list:
    """Fetch aircraft near a point from ADS-B Exchange (requires API key)."""
    api_key = getattr(settings, "adsb_exchange_api_key", "")
    if not api_key:
        return []

    resp = client.get(
        f"{ADSB_EXCHANGE_API}/lat/{lat}/lon/{lng}/dist/{radius_nm}/",
        headers={
            "Api-Auth": api_key,
            "User-Agent": "MINDEX-ETL/2.0",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("ac", [])


def map_adsb_exchange(record: dict) -> dict:
    """Map ADS-B Exchange record to MINDEX aircraft format."""
    return {
        "source": "adsb_exchange",
        "icao24": record.get("hex"),
        "callsign": (record.get("flight") or "").strip(),
        "registration": record.get("r"),
        "aircraft_type": record.get("t"),
        "origin": None,
        "destination": None,
        "lat": record.get("lat"),
        "lng": record.get("lon"),
        "altitude_ft": record.get("alt_baro"),
        "ground_speed_kts": record.get("gs"),
        "heading": record.get("track"),
        "vertical_rate": record.get("baro_rate"),
        "on_ground": record.get("alt_baro") == "ground",
        "squawk": record.get("squawk"),
        "observed_at": record.get("seen_pos"),
        "properties": {
            "category": record.get("category"),
            "nav_altitude": record.get("nav_altitude_mcp"),
            "nav_heading": record.get("nav_heading"),
            "emergency": record.get("emergency"),
            "dbFlags": record.get("dbFlags"),
        },
    }
