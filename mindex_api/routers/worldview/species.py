"""
Worldview Species Router — Read-only species, observations, genetics, compounds.

Wraps internal taxon, observations, genetics, and compounds GET endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import CallerIdentity, require_worldview_key
from ...dependencies import get_db_session, pagination_params, PaginationParams
from .response_envelope import wrap_governed_response

router = APIRouter(prefix="/species", tags=["Worldview Species & Biology"])


@router.get("/taxa")
async def worldview_list_taxa(
    request: Request,
    q: Optional[str] = Query(None, max_length=500, description="Search taxa by name"),
    rank: Optional[str] = Query(None, description="Filter by taxonomic rank"),
    source: Optional[str] = Query(None, description="Filter by data source"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Search and list taxonomic records."""
    from ...dependencies import PaginationParams
    from ..taxon import list_taxa

    request.state.caller_identity = caller
    pagination = PaginationParams(limit=limit, offset=offset)
    result = await list_taxa(pagination=pagination, db=db, q=q, rank=rank, source=source, ids=None, prefix=None, order_by="canonical_name", order="asc")
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return await wrap_governed_response(data=data, caller=caller, source_domains=["taxa", "species"])


@router.get("/taxa/{taxon_id}")
async def worldview_get_taxon(
    request: Request,
    taxon_id: UUID,
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get a specific taxon by ID."""
    from ..taxon import get_taxon

    request.state.caller_identity = caller
    result = await get_taxon(taxon_id=taxon_id, db=db)
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return await wrap_governed_response(data=data, caller=caller, source_domains=["taxa", "species"], count=1)


@router.get("/observations")
async def worldview_list_observations(
    request: Request,
    taxon_id: Optional[UUID] = Query(None),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    bbox: Optional[str] = Query(None, description="minLon,minLat,maxLon,maxLat"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """List field observations with spatial/temporal filtering."""
    from ..observations import list_observations

    request.state.caller_identity = caller
    pagination = PaginationParams(limit=limit, offset=offset)
    result = await list_observations(
        pagination=pagination, db=db,
        taxon_id=taxon_id, start=start, end=end, bbox=bbox,
    )
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return await wrap_governed_response(data=data, caller=caller, source_domains=["observations", "species"])


@router.get("/genetics")
async def worldview_list_genetics(
    request: Request,
    q: Optional[str] = Query(None, max_length=500),
    species: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Search genetic sequences."""
    from ..genetics import list_sequences

    request.state.caller_identity = caller
    pagination = PaginationParams(limit=limit, offset=offset)
    result = await list_sequences(pagination=pagination, db=db, q=q, species=species)
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return await wrap_governed_response(data=data, caller=caller, source_domains=["genetics", "species"])


@router.get("/compounds")
async def worldview_list_compounds(
    request: Request,
    q: Optional[str] = Query(None, max_length=500),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Search chemical compounds."""
    from ..compounds import list_compounds

    request.state.caller_identity = caller
    pagination = PaginationParams(limit=limit, offset=offset)
    result = await list_compounds(pagination=pagination, db=db, q=q)
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return await wrap_governed_response(data=data, caller=caller, source_domains=["compounds", "species"])
