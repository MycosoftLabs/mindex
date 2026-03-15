"""
OpenAQ — Global Air Quality Data
==================================
Open air quality data from government monitoring stations worldwide.
https://openaq.org/ / https://api.openaq.org/

Also covers EPA AirNow and PurpleAir sources.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

OPENAQ_API = "https://api.openaq.org/v2"
AIRNOW_API = "https://www.airnowapi.org/aq"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_openaq_measurements(
    client: httpx.Client,
    parameter: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    limit: int = 1000,
    page: int = 1,
) -> dict:
    """Fetch latest air quality measurements from OpenAQ."""
    params: Dict[str, Any] = {
        "limit": limit,
        "page": page,
        "order_by": "datetime",
        "sort": "desc",
    }
    if parameter:
        params["parameter"] = parameter
    if country:
        params["country"] = country
    if city:
        params["city"] = city

    resp = client.get(
        f"{OPENAQ_API}/measurements",
        params=params,
        timeout=30,
        headers={"User-Agent": "MINDEX-ETL/2.0"},
    )
    resp.raise_for_status()
    return resp.json()


def map_openaq_measurement(record: dict) -> dict:
    """Map OpenAQ measurement to MINDEX air quality format."""
    coords = record.get("coordinates", {})
    return {
        "source": "openaq",
        "source_id": str(record.get("locationId", "")),
        "station_name": record.get("location"),
        "lat": coords.get("latitude"),
        "lng": coords.get("longitude"),
        "parameter": record.get("parameter"),
        "value": record.get("value"),
        "unit": record.get("unit"),
        "measured_at": record.get("date", {}).get("utc"),
        "averaging_period": None,
        "properties": {
            "country": record.get("country"),
            "city": record.get("city"),
            "is_mobile": record.get("isMobile"),
            "entity": record.get("entity"),
            "sensor_type": record.get("sensorType"),
        },
    }


def iter_air_quality(
    *,
    parameter: Optional[str] = None,
    country: Optional[str] = None,
    limit: int = 1000,
    max_pages: Optional[int] = 5,
    delay_seconds: float = 0.5,
) -> Generator[Dict, None, None]:
    """Iterate through OpenAQ air quality measurements."""
    with httpx.Client() as client:
        page = 1
        while True:
            data = _fetch_openaq_measurements(
                client, parameter=parameter, country=country,
                limit=limit, page=page,
            )
            results = data.get("results", [])

            if not results:
                break

            for record in results:
                yield map_openaq_measurement(record)

            page += 1
            if max_pages and page > max_pages:
                break

            time.sleep(delay_seconds)


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_openaq_locations(client: httpx.Client, limit: int = 1000) -> list:
    """Fetch monitoring station locations from OpenAQ."""
    resp = client.get(
        f"{OPENAQ_API}/locations",
        params={"limit": limit, "order_by": "lastUpdated", "sort": "desc"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


# ============================================================================
# EPA AIRNOW
# ============================================================================

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_airnow_current(
    client: httpx.Client,
    bbox: Optional[Dict[str, float]] = None,
) -> list:
    """Fetch current AQI observations from AirNow."""
    api_key = getattr(settings, "airnow_api_key", "")
    if not api_key:
        return []

    params: Dict[str, Any] = {
        "format": "application/json",
        "API_KEY": api_key,
    }
    if bbox:
        url = f"{AIRNOW_API}/observation/latLong/current/"
        params.update({
            "latitude": (bbox["lat_min"] + bbox["lat_max"]) / 2,
            "longitude": (bbox["lng_min"] + bbox["lng_max"]) / 2,
            "distance": 100,
        })
    else:
        url = f"{AIRNOW_API}/observation/zipCode/current/"
        params["zipCode"] = "00000"  # Will need a valid zip

    resp = client.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def map_airnow(record: dict) -> dict:
    return {
        "source": "airnow",
        "source_id": str(record.get("StateCode", "")) + str(record.get("ReportingArea", "")),
        "station_name": record.get("ReportingArea"),
        "lat": record.get("Latitude"),
        "lng": record.get("Longitude"),
        "parameter": record.get("ParameterName", "").lower().replace(".", ""),
        "value": record.get("AQI"),
        "unit": "aqi_index",
        "measured_at": f"{record.get('DateObserved', '').strip()}T{record.get('HourObserved', '00')}:00Z",
        "properties": {
            "category": record.get("Category", {}).get("Name"),
            "state_code": record.get("StateCode"),
        },
    }
