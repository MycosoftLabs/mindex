"""
Worldview Earth Router — Read-only earth data for external users.

Wraps the internal earth router GET endpoints.
Excludes the POST /crep/sync endpoint (internal only).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import CallerIdentity, require_worldview_key
from ...dependencies import get_db_session
from .response_envelope import wrap_response

router = APIRouter(prefix="/earth", tags=["Worldview Earth Data"])


@router.get("/stats")
async def worldview_earth_stats(
    request: Request,
    caller: CallerIdentity = Depends(require_worldview_key),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get entity counts across all Earth data domains."""
    from ..earth import earth_stats

    request.state.caller_identity = caller
    result = await earth_stats(session=session)
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return wrap_response(data=data, plan=caller.plan)


@router.get("/map/bbox")
async def worldview_map_bbox(
    request: Request,
    layer: str = Query(..., description="Entity type layer to query"),
    lat_min: float = Query(...),
    lat_max: float = Query(...),
    lng_min: float = Query(...),
    lng_max: float = Query(...),
    limit: int = Query(500, ge=1, le=5000),
    caller: CallerIdentity = Depends(require_worldview_key),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get entities within a bounding box for map rendering."""
    from ..earth import map_bbox_query

    request.state.caller_identity = caller
    result = await map_bbox_query(
        layer=layer,
        lat_min=lat_min, lat_max=lat_max,
        lng_min=lng_min, lng_max=lng_max,
        limit=limit,
        session=session,
    )
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return wrap_response(data=data, plan=caller.plan)


@router.get("/map/layers")
async def worldview_map_layers(
    request: Request,
    caller: CallerIdentity = Depends(require_worldview_key),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """List available map layers."""
    from ..earth import list_map_layers

    request.state.caller_identity = caller
    result = await list_map_layers(session=session)
    data = result if isinstance(result, (dict, list)) else result
    return wrap_response(data=data, plan=caller.plan)


@router.get("/earthquakes/recent")
async def worldview_recent_earthquakes(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    min_magnitude: float = Query(0.0, ge=0.0),
    limit: int = Query(100, ge=1, le=1000),
    caller: CallerIdentity = Depends(require_worldview_key),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get recent earthquakes."""
    from ..earth import recent_earthquakes

    request.state.caller_identity = caller
    result = await recent_earthquakes(
        hours=hours, min_magnitude=min_magnitude, limit=limit, session=session,
    )
    data = result if isinstance(result, (dict, list)) else (result.model_dump() if hasattr(result, "model_dump") else result)
    return wrap_response(data=data, plan=caller.plan)


@router.get("/satellites/active")
async def worldview_active_satellites(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    caller: CallerIdentity = Depends(require_worldview_key),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get active satellites."""
    from ..earth import active_satellites

    request.state.caller_identity = caller
    result = await active_satellites(limit=limit, session=session)
    data = result if isinstance(result, (dict, list)) else (result.model_dump() if hasattr(result, "model_dump") else result)
    return wrap_response(data=data, plan=caller.plan)


@router.get("/solar/recent")
async def worldview_recent_solar(
    request: Request,
    hours: int = Query(72, ge=1, le=720),
    limit: int = Query(50, ge=1, le=500),
    caller: CallerIdentity = Depends(require_worldview_key),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get recent solar events."""
    from ..earth import recent_solar_events

    request.state.caller_identity = caller
    result = await recent_solar_events(hours=hours, limit=limit, session=session)
    data = result if isinstance(result, (dict, list)) else (result.model_dump() if hasattr(result, "model_dump") else result)
    return wrap_response(data=data, plan=caller.plan)


@router.get("/infrastructure")
async def worldview_infrastructure(
    request: Request,
    lat_min: float = Query(...),
    lat_max: float = Query(...),
    lng_min: float = Query(...),
    lng_max: float = Query(...),
    infra_type: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    caller: CallerIdentity = Depends(require_worldview_key),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get infrastructure within a bounding box."""
    from ..earth import infrastructure_bbox

    request.state.caller_identity = caller
    result = await infrastructure_bbox(
        lat_min=lat_min, lat_max=lat_max,
        lng_min=lng_min, lng_max=lng_max,
        infra_type=infra_type, limit=limit,
        session=session,
    )
    data = result if isinstance(result, (dict, list)) else (result.model_dump() if hasattr(result, "model_dump") else result)
    return wrap_response(data=data, plan=caller.plan)
