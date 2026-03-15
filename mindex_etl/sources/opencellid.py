"""
OpenCellID / FCC — Cell Tower & Antenna Infrastructure
========================================================
Cell tower locations and RF signal infrastructure data.
https://opencellid.org/
https://www.fcc.gov/

Also covers WiGLE for WiFi/Bluetooth hotspots.
"""
from __future__ import annotations

import csv
import io
import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

OPENCELLID_API = "https://opencellid.org/ajax/searchCell.php"
WIGLE_API = "https://api.wigle.net/api/v2"


# ============================================================================
# CELL TOWERS (OpenCellID)
# ============================================================================

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_cell_towers_bbox(
    client: httpx.Client,
    lat_min: float, lng_min: float,
    lat_max: float, lng_max: float,
    limit: int = 1000,
) -> list:
    """Fetch cell towers within a bounding box from OpenCellID."""
    api_key = getattr(settings, "opencellid_api_key", "")
    if not api_key:
        return []

    resp = client.get(
        "https://opencellid.org/cell/getInArea",
        params={
            "key": api_key,
            "BBOX": f"{lat_min},{lng_min},{lat_max},{lng_max}",
            "limit": limit,
            "format": "json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("cells", [])


def map_cell_tower(record: dict) -> dict:
    """Map OpenCellID record to MINDEX antenna format."""
    radio = record.get("radio", "").upper()
    tech_map = {"GSM": "GSM", "UMTS": "3G", "LTE": "4G_LTE", "NR": "5G_NR", "CDMA": "CDMA"}

    return {
        "source": "opencellid",
        "source_id": f"ocid_{record.get('cellid', '')}_{record.get('lac', '')}",
        "antenna_type": "cell_tower",
        "frequency_mhz": None,
        "band": None,
        "operator": record.get("operator"),
        "call_sign": None,
        "power_watts": None,
        "height_m": None,
        "lat": record.get("lat"),
        "lng": record.get("lon"),
        "technology": tech_map.get(radio, radio),
        "status": "active",
        "coverage_radius_m": record.get("range"),
        "properties": {
            "mcc": record.get("mcc"),
            "mnc": record.get("mnc"),
            "lac": record.get("lac"),
            "cellid": record.get("cellid"),
            "radio": radio,
            "samples": record.get("samples"),
            "changeable": record.get("changeable"),
            "averageSignal": record.get("averageSignal"),
        },
    }


# ============================================================================
# FCC ANTENNA STRUCTURE REGISTRATION
# ============================================================================

FCC_ASR_URL = "https://www.fcc.gov/api/antenna-structure-registration"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_fcc_towers(
    client: httpx.Client,
    state: Optional[str] = None,
    limit: int = 1000,
) -> list:
    """Fetch FCC registered antenna structures."""
    # FCC bulk download endpoint for antenna structures
    resp = client.get(
        "https://data.fcc.gov/api/service/antenna/structure/getList",
        params={
            "format": "json",
            "rowsPerPage": limit,
            "stateCode": state or "",
        },
        timeout=60,
        headers={"User-Agent": "MINDEX-ETL/2.0"},
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("results", [])


def map_fcc_tower(record: dict) -> dict:
    """Map FCC ASR record to MINDEX antenna format."""
    return {
        "source": "fcc",
        "source_id": f"fcc_{record.get('uniqueSystemIdentifier', '')}",
        "antenna_type": _fcc_structure_type(record.get("structureType", "")),
        "frequency_mhz": None,
        "band": None,
        "operator": record.get("entityName"),
        "call_sign": record.get("callSign"),
        "power_watts": None,
        "height_m": _feet_to_meters(record.get("overallHeightAboveGround")),
        "lat": record.get("latitude"),
        "lng": record.get("longitude"),
        "technology": None,
        "status": record.get("statusCode"),
        "properties": {
            "registration_number": record.get("registrationNumber"),
            "structure_type": record.get("structureType"),
            "faa_study_number": record.get("faaStudyNumber"),
            "height_above_ground_ft": record.get("overallHeightAboveGround"),
        },
    }


def _fcc_structure_type(stype: str) -> str:
    """Map FCC structure type to antenna_type."""
    stype = stype.upper()
    if "TOWER" in stype:
        return "cell_tower"
    if "POLE" in stype:
        return "cell_tower"
    if "BUILDING" in stype:
        return "building_mounted"
    return "cell_tower"


def _feet_to_meters(feet) -> Optional[float]:
    try:
        return round(float(feet) * 0.3048, 1)
    except (ValueError, TypeError):
        return None


# ============================================================================
# WIGLE (WiFi/Bluetooth)
# ============================================================================

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_wigle_networks(
    client: httpx.Client,
    lat_min: float, lng_min: float,
    lat_max: float, lng_max: float,
    wifi_only: bool = True,
    limit: int = 100,
) -> list:
    """Fetch WiFi/Bluetooth networks from WiGLE."""
    api_name = getattr(settings, "wigle_api_name", "")
    api_token = getattr(settings, "wigle_api_token", "")
    if not api_name or not api_token:
        return []

    endpoint = "network/search" if wifi_only else "bluetooth/search"
    resp = client.get(
        f"{WIGLE_API}/{endpoint}",
        params={
            "latrange1": lat_min, "latrange2": lat_max,
            "longrange1": lng_min, "longrange2": lng_max,
            "resultsPerPage": limit,
        },
        auth=(api_name, api_token),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def map_wigle_wifi(record: dict) -> dict:
    """Map WiGLE WiFi record to MINDEX wifi_hotspot format."""
    return {
        "source": "wigle",
        "ssid": record.get("ssid"),
        "bssid": record.get("netid"),
        "encryption": record.get("encryption"),
        "channel": record.get("channel"),
        "frequency_mhz": record.get("frequency"),
        "signal_dbm": None,
        "lat": record.get("trilat"),
        "lng": record.get("trilong"),
        "last_seen": record.get("lasttime"),
        "properties": {
            "type": record.get("type"),
            "comment": record.get("comment"),
            "wep": record.get("wep"),
            "city": record.get("city"),
            "region": record.get("region"),
            "country": record.get("country"),
        },
    }
