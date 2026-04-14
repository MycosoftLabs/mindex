"""
Worldview Search Router — Read-only unified search for external users.

Wraps the internal unified_search router but:
- Only exposes GET endpoints
- Strips internal-only domains (telemetry, devices) from results
- Uses WorldviewResponse envelope
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import CallerIdentity, require_worldview_key
from ...dependencies import get_db_session
from .response_envelope import wrap_response

router = APIRouter(prefix="/search", tags=["Worldview Search"])

# Domains that should be excluded from Worldview API results
INTERNAL_DOMAINS = frozenset({"devices", "telemetry"})

# Safe domain list for external users
WORLDVIEW_DOMAINS = [
    "taxa", "species", "compounds", "genetics", "observations",
    "earthquakes", "volcanoes", "wildfires", "storms", "lightning", "tornadoes", "floods",
    "air_quality", "greenhouse_gas", "weather", "remote_sensing",
    "buoys", "stream_gauges",
    "facilities", "power_grid", "water_systems", "internet_cables",
    "antennas", "wifi_hotspots", "signal_measurements",
    "aircraft", "vessels", "airports", "ports", "spaceports", "launches",
    "satellites", "solar_events",
    "cameras",
    "military_installations",
    "research", "crep_entities", "fusarium_tracks", "fusarium_correlations",
]


@router.get("")
async def worldview_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    domains: Optional[str] = Query(
        None,
        description="Comma-separated domain filter (e.g., 'taxa,earthquakes'). Defaults to all worldview domains.",
    ),
    lat: Optional[float] = Query(None, description="Latitude for location-based search"),
    lng: Optional[float] = Query(None, description="Longitude for location-based search"),
    radius_km: Optional[float] = Query(None, ge=0.1, le=500, description="Search radius in km"),
    limit: int = Query(50, ge=1, le=200, description="Maximum results"),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Search across all planetary data domains.

    Returns unified results from biology, earth events, atmosphere, water,
    infrastructure, signals, transport, space, and more.
    """
    # Import the internal search function
    from ..unified_search import unified_search

    # Filter requested domains to exclude internal-only ones
    if domains:
        requested = [d.strip() for d in domains.split(",")]
        safe_domains = [d for d in requested if d not in INTERNAL_DOMAINS and d in WORLDVIEW_DOMAINS]
        domain_str = ",".join(safe_domains) if safe_domains else None
    else:
        domain_str = None  # Will use all domains, internal ones filtered from results

    # Store caller identity in request state for middleware
    request.state.caller_identity = caller

    # Call internal search
    result = await unified_search(
        q=q,
        domains=domain_str,
        lat=lat,
        lng=lng,
        radius_km=radius_km,
        limit=limit,
        db=db,
    )

    # Filter out internal domains from results
    if hasattr(result, "results"):
        filtered = [r for r in result.results if r.domain not in INTERNAL_DOMAINS]
        response_data = [r.model_dump() if hasattr(r, "model_dump") else r for r in filtered]
    elif isinstance(result, dict) and "results" in result:
        filtered = [r for r in result["results"] if r.get("domain") not in INTERNAL_DOMAINS]
        response_data = filtered
    else:
        response_data = result.model_dump() if hasattr(result, "model_dump") else result

    return wrap_response(data=response_data, plan=caller.plan)


@router.get("/domains")
async def worldview_domains(
    caller: CallerIdentity = Depends(require_worldview_key),
) -> dict:
    """List all searchable domains available in the Worldview API."""
    return wrap_response(
        data={
            "domains": WORLDVIEW_DOMAINS,
            "groups": {
                "biological": ["taxa", "species", "compounds", "genetics", "observations"],
                "earth_events": ["earthquakes", "volcanoes", "wildfires", "storms", "lightning", "tornadoes", "floods"],
                "atmosphere": ["air_quality", "greenhouse_gas", "weather", "remote_sensing"],
                "water": ["buoys", "stream_gauges"],
                "infrastructure": ["facilities", "power_grid", "water_systems", "internet_cables"],
                "signals": ["antennas", "wifi_hotspots", "signal_measurements"],
                "transport": ["aircraft", "vessels", "airports", "ports", "spaceports", "launches"],
                "space": ["satellites", "solar_events"],
                "fusarium": ["fusarium_tracks", "fusarium_correlations", "crep_entities", "vessels", "buoys"],
            },
        },
        plan=caller.plan,
    )
