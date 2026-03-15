"""
NOAA Data Sources
==================
National Oceanic and Atmospheric Administration data:
- Space Weather Prediction Center (SWPC) — solar events, geomagnetic storms
- National Data Buoy Center (NDBC) — ocean buoys
- Integrated Surface Database (ISD) — weather observations
- National Weather Service (NWS) — severe weather alerts
- Global Monitoring Laboratory (GML) — CO2, methane, greenhouse gases
- Storm Prediction Center (SPC) — tornadoes, severe storms

Also covers NASA data feeds:
- SOHO, STEREO A/B solar observatory data
- DONKI (space weather events)
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

# ============================================================================
# SPACE WEATHER
# ============================================================================

SWPC_API = "https://services.swpc.noaa.gov"
DONKI_API = "https://api.nasa.gov/DONKI"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_solar_flares(client: httpx.Client, days: int = 30) -> list:
    """Fetch recent solar flare events from NASA DONKI."""
    nasa_key = getattr(settings, "nasa_api_key", "DEMO_KEY")
    resp = client.get(
        f"{DONKI_API}/FLR",
        params={"api_key": nasa_key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def map_solar_flare(record: dict) -> dict:
    return {
        "source": "noaa_donki",
        "event_type": "solar_flare",
        "class": record.get("classType"),
        "intensity": None,
        "source_region": str(record.get("activeRegionNum", "")),
        "start_time": record.get("beginTime"),
        "peak_time": record.get("peakTime"),
        "end_time": record.get("endTime"),
        "earth_directed": None,
        "properties": {
            "flr_id": record.get("flrID"),
            "instruments": record.get("instruments"),
            "linked_events": record.get("linkedEvents"),
        },
    }


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_cme_events(client: httpx.Client) -> list:
    """Fetch Coronal Mass Ejection events from NASA DONKI."""
    nasa_key = getattr(settings, "nasa_api_key", "DEMO_KEY")
    resp = client.get(
        f"{DONKI_API}/CME",
        params={"api_key": nasa_key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def map_cme(record: dict) -> dict:
    analysis = (record.get("cmeAnalyses") or [{}])[0] if record.get("cmeAnalyses") else {}
    return {
        "source": "noaa_donki",
        "event_type": "cme",
        "class": None,
        "intensity": None,
        "speed_km_s": analysis.get("speed"),
        "source_region": record.get("sourceLocation"),
        "start_time": record.get("startTime"),
        "earth_directed": analysis.get("isMostAccurate"),
        "properties": {
            "activity_id": record.get("activityID"),
            "catalog": record.get("catalog"),
            "half_angle": analysis.get("halfAngle"),
            "type": analysis.get("type"),
        },
    }


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_geomagnetic_storms(client: httpx.Client) -> list:
    """Fetch geomagnetic storm events from NASA DONKI."""
    nasa_key = getattr(settings, "nasa_api_key", "DEMO_KEY")
    resp = client.get(
        f"{DONKI_API}/GST",
        params={"api_key": nasa_key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def map_geomagnetic_storm(record: dict) -> dict:
    kp_index = None
    if record.get("allKpIndex"):
        kp_values = [k.get("kpIndex") for k in record["allKpIndex"] if k.get("kpIndex")]
        kp_index = max(kp_values) if kp_values else None

    return {
        "source": "noaa_donki",
        "event_type": "geomagnetic_storm",
        "class": None,
        "kp_index": kp_index,
        "start_time": record.get("startTime"),
        "properties": {
            "gst_id": record.get("gstID"),
            "linked_events": record.get("linkedEvents"),
            "all_kp": record.get("allKpIndex"),
        },
    }


def iter_solar_events() -> Generator[Dict, None, None]:
    """Iterate all solar/space weather events."""
    with httpx.Client() as client:
        # Solar flares
        try:
            for flare in fetch_solar_flares(client):
                yield map_solar_flare(flare)
        except Exception:
            pass

        # CMEs
        try:
            for cme in fetch_cme_events(client):
                yield map_cme(cme)
        except Exception:
            pass

        # Geomagnetic storms
        try:
            for storm in fetch_geomagnetic_storms(client):
                yield map_geomagnetic_storm(storm)
        except Exception:
            pass


# ============================================================================
# OCEAN BUOYS (NDBC)
# ============================================================================

NDBC_API = "https://www.ndbc.noaa.gov/data/realtime2"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_ndbc_active_stations(client: httpx.Client) -> list:
    """Fetch active NDBC station list."""
    resp = client.get(
        "https://www.ndbc.noaa.gov/data/stations/station_table.txt",
        timeout=30,
    )
    resp.raise_for_status()
    lines = resp.text.strip().split("\n")
    stations = []
    for line in lines[2:]:  # Skip headers
        parts = line.split("|")
        if len(parts) >= 6:
            stations.append({
                "station_id": parts[0].strip(),
                "owner": parts[1].strip(),
                "type": parts[2].strip(),
                "lat": parts[3].strip() if len(parts) > 3 else None,
                "lng": parts[4].strip() if len(parts) > 4 else None,
                "name": parts[5].strip() if len(parts) > 5 else None,
            })
    return stations


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_buoy_latest(client: httpx.Client, station_id: str) -> Optional[dict]:
    """Fetch latest observation from an NDBC buoy."""
    resp = client.get(
        f"{NDBC_API}/{station_id}.txt",
        timeout=15,
    )
    if resp.status_code != 200:
        return None

    lines = resp.text.strip().split("\n")
    if len(lines) < 3:
        return None

    headers = lines[0].split()
    values = lines[2].split()  # Skip units line
    if len(values) != len(headers):
        return None

    return dict(zip(headers, values))


def map_buoy_observation(station: dict, obs: dict) -> dict:
    """Map NDBC data to MINDEX buoy format."""
    def _float(v):
        try:
            f = float(v)
            return f if f != 99.0 and f != 999.0 and f != 9999.0 else None
        except (ValueError, TypeError):
            return None

    return {
        "source": "ndbc",
        "station_id": station.get("station_id", ""),
        "name": station.get("name"),
        "buoy_type": station.get("type", "weather"),
        "lat": _float(station.get("lat")),
        "lng": _float(station.get("lng")),
        "water_temp_c": _float(obs.get("WTMP")),
        "wave_height_m": _float(obs.get("WVHT")),
        "wave_period_s": _float(obs.get("DPD")),
        "wind_speed_ms": _float(obs.get("WSPD")),
        "wind_direction": _float(obs.get("WDIR")),
        "pressure_hpa": _float(obs.get("PRES")),
        "air_temp_c": _float(obs.get("ATMP")),
        "observed_at": f"{obs.get('#YY', '2026')}-{obs.get('MM', '01')}-{obs.get('DD', '01')}T{obs.get('hh', '00')}:{obs.get('mm', '00')}Z",
        "properties": {
            "dewpoint_c": _float(obs.get("DEWP")),
            "visibility_nmi": _float(obs.get("VIS")),
            "tide_ft": _float(obs.get("TIDE")),
        },
    }


# ============================================================================
# WEATHER (NWS / ISD)
# ============================================================================

NWS_API = "https://api.weather.gov"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_nws_alerts(client: httpx.Client, area: Optional[str] = None) -> list:
    """Fetch active NWS weather alerts."""
    params = {"status": "actual", "message_type": "alert"}
    if area:
        params["area"] = area

    resp = client.get(
        f"{NWS_API}/alerts/active",
        params=params,
        timeout=30,
        headers={"User-Agent": "MINDEX-ETL/2.0", "Accept": "application/geo+json"},
    )
    resp.raise_for_status()
    return resp.json().get("features", [])


def map_nws_alert(feature: dict) -> dict:
    """Map NWS alert to generic storm/weather event."""
    props = feature.get("properties", {})
    return {
        "source": "nws",
        "source_id": props.get("id"),
        "name": props.get("headline"),
        "storm_type": props.get("event"),
        "status": props.get("status"),
        "observed_at": props.get("effective"),
        "properties": {
            "severity": props.get("severity"),
            "certainty": props.get("certainty"),
            "urgency": props.get("urgency"),
            "sender": props.get("senderName"),
            "description": props.get("description"),
            "instruction": props.get("instruction"),
            "areas": props.get("areaDesc"),
            "expires": props.get("expires"),
        },
    }


# ============================================================================
# GREENHOUSE GASES (GML)
# ============================================================================

GML_API = "https://gml.noaa.gov/webdata"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_co2_trends(client: httpx.Client) -> list:
    """Fetch global CO2 trend data from NOAA GML."""
    resp = client.get(
        f"{GML_API}/ccgg/trends/co2/co2_trend_gl.csv",
        timeout=30,
    )
    resp.raise_for_status()
    lines = resp.text.strip().split("\n")
    results = []
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) >= 3:
            results.append({
                "year": parts[0].strip(),
                "month": parts[1].strip(),
                "value": parts[2].strip(),
            })
    return results


def map_co2_measurement(record: dict) -> dict:
    return {
        "source": "noaa_gml",
        "gas_type": "co2",
        "value": float(record.get("value", 0)),
        "unit": "ppm",
        "station_name": "Mauna Loa (Global Trend)",
        "measured_at": f"{record.get('year')}-{record.get('month', '01').zfill(2)}-15",
        "properties": {},
    }
