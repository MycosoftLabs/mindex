"""
AIS Marine Vessel Tracking
============================
Automatic Identification System data for maritime vessel tracking.
Sources: MarineTraffic, AISHub, UN-LOCODE for ports.

Also includes port and shipping infrastructure data.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

# ============================================================================
# AIS HUB (Community AIS data)
# ============================================================================

AISHUB_API = "https://data.aishub.net/ws.php"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_aishub_vessels(
    client: httpx.Client,
    bbox: Optional[Dict[str, float]] = None,
) -> list:
    """Fetch vessel positions from AISHub."""
    api_key = getattr(settings, "aishub_api_key", "")
    if not api_key:
        return []

    params: Dict[str, Any] = {
        "username": api_key,
        "format": "1",  # JSON
        "output": "json",
        "compress": "0",
    }
    if bbox:
        params.update({
            "latmin": bbox.get("lat_min"),
            "latmax": bbox.get("lat_max"),
            "lonmin": bbox.get("lng_min"),
            "lonmax": bbox.get("lng_max"),
        })

    resp = client.get(AISHUB_API, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("data", [])


def map_ais_vessel(record: dict) -> dict:
    """Map AIS record to MINDEX vessel format."""
    return {
        "source": "aishub",
        "mmsi": str(record.get("MMSI", "")),
        "imo": str(record.get("IMO", "")) if record.get("IMO") else None,
        "name": record.get("NAME"),
        "vessel_type": _vessel_type_name(record.get("TYPE")),
        "flag": record.get("FLAG"),
        "lat": record.get("LATITUDE"),
        "lng": record.get("LONGITUDE"),
        "speed_knots": record.get("SOG"),
        "course": record.get("COG"),
        "heading": record.get("HEADING"),
        "destination": record.get("DEST"),
        "draught_m": record.get("DRAUGHT"),
        "nav_status": _nav_status_name(record.get("NAVSTAT")),
        "observed_at": record.get("TIME"),
        "properties": {
            "callsign": record.get("CALLSIGN"),
            "length": record.get("LENGTH"),
            "width": record.get("WIDTH"),
            "eta": record.get("ETA"),
        },
    }


def _vessel_type_name(code) -> str:
    """Convert AIS vessel type code to human name."""
    if code is None:
        return "unknown"
    code = int(code)
    type_map = {
        30: "fishing", 31: "towing", 32: "towing_large",
        33: "dredging", 34: "diving_ops", 35: "military_ops",
        36: "sailing", 37: "pleasure_craft",
        40: "high_speed_craft", 50: "pilot_vessel",
        51: "search_and_rescue", 52: "tug",
        53: "port_tender", 55: "law_enforcement",
        60: "passenger", 70: "cargo", 80: "tanker",
    }
    for base, name in type_map.items():
        if base <= code < base + 10:
            return name
    return f"type_{code}"


def _nav_status_name(code) -> str:
    """Convert AIS navigation status code to human name."""
    status_map = {
        0: "under_way_engine", 1: "at_anchor", 2: "not_under_command",
        3: "restricted_maneuverability", 4: "constrained_by_draught",
        5: "moored", 6: "aground", 7: "engaged_in_fishing",
        8: "under_way_sailing", 15: "not_defined",
    }
    return status_map.get(code, "unknown") if code is not None else "unknown"


# ============================================================================
# WORLD PORT INDEX
# ============================================================================

def iter_world_ports() -> Generator[Dict, None, None]:
    """Iterate through world port data from NGA World Port Index."""
    url = "https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/UpdatedPub150bk.csv"
    with httpx.Client() as client:
        try:
            resp = client.get(url, timeout=60, follow_redirects=True)
            resp.raise_for_status()
            lines = resp.text.strip().split("\n")
            if len(lines) < 2:
                return

            headers = [h.strip().strip('"') for h in lines[0].split(",")]
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) >= len(headers):
                    record = dict(zip(headers, [p.strip().strip('"') for p in parts]))
                    lat = _parse_coord(record.get("Latitude"))
                    lng = _parse_coord(record.get("Longitude"))
                    if lat and lng:
                        yield {
                            "source": "nga_wpi",
                            "source_id": record.get("World Port Index Number"),
                            "name": record.get("Main Port Name", "Unknown"),
                            "port_type": "seaport",
                            "unlocode": record.get("UN/LOCODE"),
                            "lat": lat,
                            "lng": lng,
                            "country": record.get("Country Code"),
                            "properties": {
                                "harbor_size": record.get("Harbor Size"),
                                "harbor_type": record.get("Harbor Type"),
                                "shelter": record.get("Shelter Afforded"),
                                "max_vessel_length": record.get("Maximum Vessel Length"),
                            },
                        }
        except Exception:
            pass


def _parse_coord(value: Optional[str]) -> Optional[float]:
    """Parse coordinate string to float."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
