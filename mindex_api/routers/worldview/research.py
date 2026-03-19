"""
Worldview Research Router — Read-only research papers and database stats.

Wraps internal research and stats GET endpoints.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import CallerIdentity, require_worldview_key
from ...db import get_db
from ...dependencies import get_db_session
from .response_envelope import wrap_response

router = APIRouter(prefix="/research", tags=["Worldview Research & Stats"])


@router.get("/papers")
async def worldview_search_papers(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    caller: CallerIdentity = Depends(require_worldview_key),
) -> dict:
    """Search research papers from OpenAlex."""
    from ..research import search_research

    request.state.caller_identity = caller
    result = await search_research(q=q, page=1, per_page=limit)
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return wrap_response(data=data, plan=caller.plan)


@router.get("/papers/{paper_id}")
async def worldview_get_paper(
    request: Request,
    paper_id: str,
    caller: CallerIdentity = Depends(require_worldview_key),
) -> dict:
    """Get a specific research paper by ID."""
    from ..research import get_paper_detail

    request.state.caller_identity = caller
    result = await get_paper_detail(paper_id=paper_id)
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return wrap_response(data=data, count=1, plan=caller.plan)


@router.get("/stats")
async def worldview_stats(
    request: Request,
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get MINDEX database statistics."""
    from ..stats import get_statistics

    request.state.caller_identity = caller
    result = await get_statistics(db=db)
    data = result.model_dump() if hasattr(result, "model_dump") else result
    return wrap_response(data=data, count=1, plan=caller.plan)
