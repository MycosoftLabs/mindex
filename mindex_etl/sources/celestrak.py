"""
CelesTrak / Space-Track — Satellite Tracking
===============================================
Two-Line Element (TLE) data and satellite catalog from CelesTrak.
https://celestrak.org/

Covers all tracked objects in Earth orbit:
- Active satellites (weather, GPS, comms, Earth observation)
- Debris and inactive objects
- Space stations
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

CELESTRAK_API = "https://celestrak.org"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_satellite_catalog(
    client: httpx.Client,
    group: str = "active",
    format: str = "json",
) -> list:
    """Fetch satellite data from CelesTrak GP data API."""
    resp = client.get(
        f"{CELESTRAK_API}/NORAD/elements/gp.php",
        params={"GROUP": group, "FORMAT": format},
        timeout=60,
        headers={"User-Agent": "MINDEX-ETL/2.0"},
    )
    resp.raise_for_status()
    return resp.json()


def map_satellite(record: dict) -> dict:
    """Map CelesTrak GP record to MINDEX satellite format."""
    return {
        "source": "celestrak",
        "norad_id": record.get("NORAD_CAT_ID"),
        "cospar_id": record.get("OBJECT_ID"),
        "name": record.get("OBJECT_NAME", "").strip(),
        "satellite_type": _classify_satellite(record.get("OBJECT_NAME", ""), record.get("OBJECT_TYPE", "")),
        "operator": None,
        "launch_date": record.get("LAUNCH_DATE"),
        "orbit_type": _classify_orbit(
            record.get("PERIOD"),
            record.get("INCLINATION"),
            record.get("APOAPSIS"),
        ),
        "perigee_km": record.get("PERIAPSIS"),
        "apogee_km": record.get("APOAPSIS"),
        "inclination_deg": record.get("INCLINATION"),
        "period_min": record.get("PERIOD"),
        "tle_line1": record.get("TLE_LINE1"),
        "tle_line2": record.get("TLE_LINE2"),
        "tle_epoch": record.get("EPOCH"),
        "status": "active" if record.get("DECAY_DATE") is None else "decayed",
        "properties": {
            "rcs_size": record.get("RCS_SIZE"),
            "country_code": record.get("COUNTRY_CODE"),
            "site": record.get("SITE"),
            "mean_motion": record.get("MEAN_MOTION"),
            "eccentricity": record.get("ECCENTRICITY"),
            "rev_at_epoch": record.get("REV_AT_EPOCH"),
            "bstar": record.get("BSTAR"),
            "element_set_no": record.get("ELEMENT_SET_NO"),
        },
    }


def _classify_orbit(period, inclination, apoapsis) -> str:
    """Classify orbit type from orbital elements."""
    try:
        period = float(period or 0)
        incl = float(inclination or 0)
        apo = float(apoapsis or 0)
    except (ValueError, TypeError):
        return "unknown"

    if apo > 35000:
        return "GEO" if 95 <= incl <= 115 or incl < 5 else "HEO"
    if apo > 2000:
        return "MEO"
    if 95 <= incl <= 100:
        return "SSO"
    return "LEO"


def _classify_satellite(name: str, obj_type: str) -> str:
    """Classify satellite type from name and object type."""
    name_upper = name.upper()
    if "DEB" in obj_type or "DEBRIS" in name_upper:
        return "debris"
    if "R/B" in obj_type or "ROCKET" in name_upper:
        return "rocket_body"
    if any(kw in name_upper for kw in ("GPS", "NAVSTAR", "GLONASS", "GALILEO", "BEIDOU")):
        return "gps"
    if any(kw in name_upper for kw in ("NOAA", "GOES", "METEOSAT", "HIMAWARI", "JPSS")):
        return "weather"
    if any(kw in name_upper for kw in ("STARLINK", "ONEWEB", "IRIDIUM", "INTELSAT", "SES")):
        return "comm"
    if any(kw in name_upper for kw in ("LANDSAT", "SENTINEL", "TERRA", "AQUA", "MODIS", "VIIRS", "SOHO", "STEREO", "SDO")):
        return "earth_obs"
    if any(kw in name_upper for kw in ("ISS", "TIANGONG", "CSS")):
        return "space_station"
    return "active"


SATELLITE_GROUPS = [
    "active", "stations", "visual", "weather", "noaa", "goes",
    "earth-resources", "geodetic", "navigation", "gps-ops",
    "galileo", "beidou", "starlink", "oneweb",
    "science", "military", "amateur", "engineering",
]


def iter_satellites(
    *,
    groups: Optional[List[str]] = None,
    delay_seconds: float = 1.0,
) -> Generator[Dict, None, None]:
    """Iterate through satellite data from CelesTrak."""
    groups = groups or ["active"]
    seen_norad = set()

    with httpx.Client() as client:
        for group in groups:
            try:
                records = _fetch_satellite_catalog(client, group=group)
                for record in records:
                    norad = record.get("NORAD_CAT_ID")
                    if norad and norad not in seen_norad:
                        seen_norad.add(norad)
                        yield map_satellite(record)
            except Exception:
                pass

            time.sleep(delay_seconds)
