"""
Worldview Answers Router — Read-only worldview facts and QA pairs.

Wraps internal search_answers GET endpoints only.
POST endpoints (MAS orchestrator writes) stay internal.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import CallerIdentity, require_worldview_key
from ...dependencies import get_db_session
from .response_envelope import wrap_response

router = APIRouter(prefix="/answers", tags=["Worldview Answers & Knowledge"])


@router.get("/search")
async def worldview_search_answers(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(10, ge=1, le=50),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Search cached answer snippets and QA pairs by query text.

    Returns previously computed answers, worldview facts, and QA pairs
    matching the query for instant retrieval.
    """
    from ..search_answers import search_answers

    request.state.caller_identity = caller
    results = await search_answers(q=q, limit=limit, db=db)
    return wrap_response(data=results, plan=caller.plan)


@router.get("/qa")
async def worldview_list_qa(
    request: Request,
    q: Optional[str] = Query(None, max_length=500),
    limit: int = Query(20, ge=1, le=100),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """List QA pairs, optionally filtered by question text."""
    from ..search_answers import list_qa

    request.state.caller_identity = caller
    results = await list_qa(q=q, limit=limit, db=db)
    return wrap_response(data=results, plan=caller.plan)


@router.get("/worldview-facts")
async def worldview_facts(
    request: Request,
    category: Optional[str] = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Get curated worldview facts — distilled knowledge about the planet.

    These are high-quality, verified facts with provenance tracking
    and freshness expiration.
    """
    from sqlalchemy import text

    request.state.caller_identity = caller

    try:
        if category:
            stmt = text("""
                SELECT id, fact_text, category, source_type, provenance, freshness_until, created_at
                FROM search.worldview_fact
                WHERE category = :category
                  AND (freshness_until IS NULL OR freshness_until > now())
                ORDER BY created_at DESC
                LIMIT :limit
            """)
            r = await db.execute(stmt, {"category": category, "limit": limit})
        else:
            stmt = text("""
                SELECT id, fact_text, category, source_type, provenance, freshness_until, created_at
                FROM search.worldview_fact
                WHERE freshness_until IS NULL OR freshness_until > now()
                ORDER BY created_at DESC
                LIMIT :limit
            """)
            r = await db.execute(stmt, {"limit": limit})

        facts = [
            {
                "id": str(row[0]),
                "fact_text": row[1],
                "category": row[2],
                "source_type": row[3],
                "freshness_until": row[5].isoformat() if row[5] else None,
                "created_at": row[6].isoformat() if row[6] else None,
            }
            for row in r.fetchall()
        ]
    except Exception:
        facts = []

    return wrap_response(data=facts, plan=caller.plan)
