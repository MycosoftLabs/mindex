"""
Worldview Search Router — Read-only unified search for external users.

Wraps the internal unified_search router but:
- Only exposes GET endpoints
- Strips internal-only domains (telemetry, devices) from results
- Uses WorldviewResponse envelope
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import CallerIdentity, require_worldview_key
from ...dependencies import get_db_session
from .response_envelope import wrap_governed_response, wrap_response

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
        safe_domains = list(dict.fromkeys(d for d in requested if d in WORLDVIEW_DOMAINS))
        if not safe_domains:
            raise HTTPException(status_code=400, detail="No supported Worldview domains requested")
    else:
        safe_domains = WORLDVIEW_DOMAINS

    # Store caller identity in request state for middleware
    request.state.caller_identity = caller

    # Call internal search
    result = await unified_search(
        q=q,
        types=",".join(safe_domains),
        lat=lat,
        lng=lng,
        radius=radius_km if radius_km is not None else 100,
        limit=limit,
        toxicity=None,
        kingdom=None,
        facility_type=None,
        since=None,
        until=None,
        session=db,
    )

    # Internal search returns domain-keyed lists; public search exposes a flat list.
    buckets = result.results if hasattr(result, "results") else result["results"]
    response_data = []
    for domain in safe_domains:
        for item in buckets.get(domain, []):
            row = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            if row.get("domain") in INTERNAL_DOMAINS:
                continue
            row.setdefault("domain", domain)
            response_data.append(row)
    response_data = response_data[:limit]

    return await wrap_governed_response(
        data=response_data,
        caller=caller,
        source_domains=safe_domains,
        region={"lat": lat, "lng": lng, "radius_km": radius_km} if lat is not None and lng is not None else None,
    )


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
