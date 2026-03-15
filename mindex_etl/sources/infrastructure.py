"""
Infrastructure Data Sources
=============================
Facilities, power plants, factories, mining, dams, water treatment, etc.

Sources:
- EPA FRS (Facility Registry Service)
- EIA (Energy Information Administration) — power plants
- USGS — dams and water resources
- OSM (OpenStreetMap) — infrastructure features
- Submarine Cable Map — internet cables
- TeleGeography — cable data
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings


# ============================================================================
# EPA FACILITY REGISTRY SERVICE
# ============================================================================

EPA_FRS_API = "https://enviro.epa.gov/enviro/efservice"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_epa_facilities(
    client: httpx.Client,
    state: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
) -> list:
    """Fetch regulated facilities from EPA FRS."""
    params: Dict[str, Any] = {
        "p_format": "json",
        "p_limit": limit,
        "p_offset": offset,
    }
    if state:
        params["p_state"] = state

    resp = client.get(
        "https://data.epa.gov/efservice/FRS_PROGRAM_FACILITY/JSON",
        params=params,
        timeout=60,
        headers={"User-Agent": "MINDEX-ETL/2.0"},
    )
    resp.raise_for_status()
    return resp.json()


def map_epa_facility(record: dict) -> dict:
    """Map EPA FRS facility to MINDEX facility format."""
    return {
        "source": "epa",
        "source_id": record.get("REGISTRY_ID"),
        "name": record.get("PRIMARY_NAME", "Unknown Facility"),
        "facility_type": _classify_epa_facility(record),
        "sub_type": record.get("SIC_CODES"),
        "lat": record.get("LATITUDE83"),
        "lng": record.get("LONGITUDE83"),
        "address": record.get("LOCATION_ADDRESS"),
        "city": record.get("CITY_NAME"),
        "state_province": record.get("STATE_CODE"),
        "country": "US",
        "operator": record.get("ORG_NAME"),
        "status": "active",
        "properties": {
            "registry_id": record.get("REGISTRY_ID"),
            "naics_codes": record.get("NAICS_CODES"),
            "sic_codes": record.get("SIC_CODES"),
            "federal_facility": record.get("FEDERAL_FACILITY_CODE"),
            "programs": record.get("PGM_SYS_ACRNMS"),
        },
    }


def _classify_epa_facility(record: dict) -> str:
    """Classify EPA facility type from program codes."""
    programs = (record.get("PGM_SYS_ACRNMS") or "").upper()
    if "CAMDBS" in programs or "EGRID" in programs:
        return "power_plant"
    if "RCRA" in programs:
        return "waste"
    if "SDWIS" in programs:
        return "water_treatment"
    if "TRI" in programs:
        return "factory"
    return "industrial"


# ============================================================================
# EIA POWER PLANTS
# ============================================================================

EIA_API = "https://api.eia.gov/v2"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_eia_power_plants(client: httpx.Client, limit: int = 5000) -> list:
    """Fetch US power plant data from EIA."""
    api_key = getattr(settings, "eia_api_key", "")
    if not api_key:
        return []

    resp = client.get(
        f"{EIA_API}/electricity/facility-fuel/data/",
        params={
            "api_key": api_key,
            "frequency": "annual",
            "data[]": "generation",
            "length": limit,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("response", {}).get("data", [])


def map_eia_plant(record: dict) -> dict:
    """Map EIA power plant to MINDEX facility format."""
    fuel_map = {
        "SUN": "solar", "WND": "wind", "WAT": "hydro", "NUC": "nuclear",
        "NG": "natural_gas", "SUB": "coal", "BIT": "coal", "LIG": "coal",
        "PC": "petroleum", "DFO": "diesel", "GEO": "geothermal",
        "WDS": "biomass", "OBG": "biogas", "MSW": "waste_to_energy",
    }
    fuel = record.get("fuel2002", "")
    return {
        "source": "eia",
        "source_id": str(record.get("plantid", "")),
        "name": record.get("plantName", "Unknown Plant"),
        "facility_type": "power_plant",
        "sub_type": fuel_map.get(fuel, fuel),
        "lat": record.get("latitude"),
        "lng": record.get("longitude"),
        "state_province": record.get("stateDescription"),
        "country": "US",
        "operator": record.get("operator"),
        "capacity": record.get("nameplate-capacity-mw"),
        "properties": {
            "plant_id": record.get("plantid"),
            "fuel_type": fuel,
            "generation_mwh": record.get("generation"),
            "state": record.get("state"),
            "sector": record.get("sectorName"),
        },
    }


# ============================================================================
# SUBMARINE CABLES
# ============================================================================

SUBMARINE_CABLE_URL = "https://raw.githubusercontent.com/telegeography/www.submarinecablemap.com/master/web/public/api/v3/cable/cable-geo.json"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_submarine_cables(client: httpx.Client) -> list:
    """Fetch submarine cable data from TeleGeography."""
    resp = client.get(SUBMARINE_CABLE_URL, timeout=60)
    resp.raise_for_status()
    return resp.json().get("features", [])


def map_submarine_cable(feature: dict) -> dict:
    """Map GeoJSON cable feature to MINDEX internet_cable format."""
    props = feature.get("properties", {})
    return {
        "source": "submarinecablemap",
        "source_id": props.get("id"),
        "name": props.get("name", "Unknown Cable"),
        "cable_type": "submarine",
        "length_km": props.get("length", "").replace(" km", "").replace(",", ""),
        "capacity_tbps": None,
        "status": props.get("is_planned") and "planned" or "active",
        "owners": props.get("owners"),
        "rfs_date": props.get("rfs"),
        "landing_points": props.get("landing_points"),
        "properties": {
            "url": props.get("url"),
            "color": props.get("color"),
        },
    }


def iter_submarine_cables() -> Generator[Dict, None, None]:
    """Iterate through submarine cable data."""
    with httpx.Client() as client:
        features = fetch_submarine_cables(client)
        for feature in features:
            yield map_submarine_cable(feature)


# ============================================================================
# USGS DAMS (National Inventory of Dams)
# ============================================================================

NID_URL = "https://nid.sec.usace.army.mil/api/nation/csv"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def fetch_us_dams(client: httpx.Client, limit: int = 5000) -> list:
    """Fetch dam data from US Army Corps of Engineers NID."""
    resp = client.get(
        "https://nid.sec.usace.army.mil/api/nation/geojson",
        params={"limit": limit},
        timeout=120,
        headers={"User-Agent": "MINDEX-ETL/2.0"},
    )
    if resp.status_code != 200:
        return []
    return resp.json().get("features", [])


def map_dam(feature: dict) -> dict:
    """Map NID dam feature to MINDEX water_system format."""
    props = feature.get("properties", {})
    coords = feature.get("geometry", {}).get("coordinates", [])
    return {
        "source": "usace_nid",
        "source_id": props.get("nidId"),
        "system_type": "dam",
        "name": props.get("name", "Unknown Dam"),
        "lat": coords[1] if len(coords) > 1 else None,
        "lng": coords[0] if len(coords) > 0 else None,
        "capacity": props.get("maxStorage"),
        "properties": {
            "dam_type": props.get("damType"),
            "primary_purpose": props.get("primaryPurpose"),
            "height_ft": props.get("damHeight"),
            "length_ft": props.get("damLength"),
            "year_completed": props.get("yearCompleted"),
            "hazard_potential": props.get("hazardPotential"),
            "condition_assessment": props.get("conditionAssessment"),
            "state": props.get("state"),
            "county": props.get("county"),
            "river": props.get("river"),
            "owner_type": props.get("ownerType"),
        },
    }
