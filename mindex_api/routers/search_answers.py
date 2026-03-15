"""
Search Answers Router — answer/QA/worldview schema for instant second-search.

- GET /search/answers?q=... — search answer_snippet, qa_pair, worldview_fact (second-search retrieval)
- POST /search/answers — upsert answer snippet (MAS write)
- POST /search/queries — record normalized query
- GET /search/qa — list/search QA pairs
- POST /search/qa — upsert QA pair

Schema: search.query, search.answer_snippet, search.qa_pair, search.worldview_fact, search.answer_source.
Created: March 14, 2026
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

router = APIRouter(prefix="/search", tags=["Search Answers"])


# --- Schemas ---


class AnswerSnippetCreate(BaseModel):
    """Create or update answer snippet."""
    normalized_query: str
    snippet_text: str
    source_type: str = "orchestrator"
    source_id: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class AnswerSnippetOut(BaseModel):
    """Answer snippet response."""
    id: str
    snippet_text: str
    source_type: str
    source_id: Optional[str]
    provenance: Dict[str, Any]
    created_at: datetime


class QueryRecordCreate(BaseModel):
    """Record a normalized query."""
    normalized_query: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class QAPairCreate(BaseModel):
    """Create or update QA pair."""
    question_normalized: str
    answer_text: str
    source_type: str = "orchestrator"
    source_id: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)


class WorldviewFactCreate(BaseModel):
    """Create worldview fact."""
    fact_text: str
    category: Optional[str] = None
    source_type: str = "orchestrator"
    source_id: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)


# --- Helpers ---


def _query_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.strip().lower().encode()).hexdigest()[:32]


# --- Endpoints ---


@router.get("/answers", response_model=List[Dict[str, Any]])
async def search_answers(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """
    Instant second-search: look up cached answers by query text.
    Queries answer_snippet and qa_pair by normalized query / text.
    """
    qn = q.strip().lower()
    qh = _query_hash(qn)
    results: List[Dict[str, Any]] = []

    try:
        # Answer snippets matching query (by result_hash or linked query hash)
        stmt = text(
            """
            SELECT id, snippet_text, source_type, source_id, provenance, created_at
            FROM search.answer_snippet
            WHERE query_id IN (SELECT id FROM search.query WHERE query_hash = :qh)
               OR result_hash = :qh
            ORDER BY created_at DESC
            LIMIT :limit
            """
        )
        r = await db.execute(stmt, {"qh": qh, "limit": limit})
        for row in r.fetchall():
            results.append({
                "type": "snippet",
                "id": str(row[0]),
                "snippet_text": row[1],
                "source_type": row[2],
                "source_id": row[3],
                "provenance": row[4] or {},
                "created_at": row[5].isoformat() if row[5] else None,
            })

        # QA pairs with similar question
        stmt2 = text(
            """
            SELECT id, question_normalized, answer_text, source_type, created_at
            FROM search.qa_pair
            WHERE question_normalized ILIKE :q
            ORDER BY updated_at DESC
            LIMIT :limit
            """
        )
        r2 = await db.execute(stmt2, {"q": f"%{qn}%", "limit": limit})
        for row in r2.fetchall():
            results.append({
                "type": "qa",
                "id": str(row[0]),
                "question": row[1],
                "answer_text": row[2],
                "source_type": row[3],
                "created_at": row[4].isoformat() if row[4] else None,
            })
    except Exception:
        # Schema may not exist yet
        pass

    return results[:limit]


@router.post("/answers", response_model=AnswerSnippetOut)
async def upsert_answer_snippet(
    body: AnswerSnippetCreate,
    db: AsyncSession = Depends(get_db_session),
) -> AnswerSnippetOut:
    """Upsert an answer snippet (MAS orchestrator write)."""
    qh = _query_hash(body.normalized_query)
    import json
    prov = json.dumps(body.provenance)

    try:
        # Insert query and get id
        stmt_q = text(
            """
            INSERT INTO search.query (normalized_query, query_hash, session_id, user_id)
            VALUES (:norm, :qh, :session_id, :user_id)
            RETURNING id
            """
        )
        rq = await db.execute(stmt_q, {
            "norm": body.normalized_query.strip(),
            "qh": qh,
            "session_id": body.session_id,
            "user_id": body.user_id,
        })
        row_q = rq.fetchone()
        query_id = str(row_q[0]) if row_q else None

        # Insert answer_snippet
        stmt = text(
            """
            INSERT INTO search.answer_snippet
            (query_id, snippet_text, source_type, source_id, provenance, result_hash)
            VALUES (:query_id::uuid, :snippet_text, :source_type, :source_id, :provenance::jsonb, :result_hash)
            RETURNING id, snippet_text, source_type, source_id, provenance, created_at
            """
        )
        result = await db.execute(stmt, {
            "query_id": query_id,
            "snippet_text": body.snippet_text,
            "source_type": body.source_type,
            "source_id": body.source_id,
            "provenance": prov,
            "result_hash": qh,
        })
        row = result.fetchone()
        await db.commit()
        return AnswerSnippetOut(
            id=str(row[0]),
            snippet_text=row[1],
            source_type=row[2],
            source_id=row[3],
            provenance=row[4] or {},
            created_at=row[5],
        )
    except Exception as e:
        await db.rollback()
        raise e


@router.post("/queries", response_model=Dict[str, str])
async def record_query(
    body: QueryRecordCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, str]:
    """Record a normalized query for analytics and second-search lookup."""
    qh = _query_hash(body.normalized_query)
    try:
        stmt = text(
            """
            INSERT INTO search.query (normalized_query, query_hash, session_id, user_id)
            VALUES (:norm, :qh, :session_id, :user_id)
            RETURNING id
            """
        )
        r = await db.execute(stmt, {
            "norm": body.normalized_query.strip(),
            "qh": qh,
            "session_id": body.session_id,
            "user_id": body.user_id,
        })
        row = r.fetchone()
        await db.commit()
        return {"id": str(row[0]), "query_hash": qh}
    except Exception as e:
        await db.rollback()
        raise e


@router.get("/qa", response_model=List[Dict[str, Any]])
async def list_qa(
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """List QA pairs, optionally filtered by question text."""
    try:
        if q:
            stmt = text(
                """
                SELECT id, question_normalized, answer_text, source_type, created_at
                FROM search.qa_pair
                WHERE question_normalized ILIKE :q
                ORDER BY updated_at DESC
                LIMIT :limit
                """
            )
            r = await db.execute(stmt, {"q": f"%{q}%", "limit": limit})
        else:
            stmt = text(
                """
                SELECT id, question_normalized, answer_text, source_type, created_at
                FROM search.qa_pair
                ORDER BY updated_at DESC
                LIMIT :limit
                """
            )
            r = await db.execute(stmt, {"limit": limit})
        return [
            {
                "id": str(row[0]),
                "question": row[1],
                "answer_text": row[2],
                "source_type": row[3],
                "created_at": row[4].isoformat() if row[4] else None,
            }
            for row in r.fetchall()
        ]
    except Exception:
        return []


@router.post("/qa", response_model=Dict[str, Any])
async def upsert_qa_pair(
    body: QAPairCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Upsert a QA pair."""
    import json
    try:
        stmt = text(
            """
            INSERT INTO search.qa_pair (question_normalized, answer_text, source_type, source_id, provenance)
            VALUES (:q, :a, :source_type, :source_id, :provenance::jsonb)
            RETURNING id, question_normalized, answer_text, source_type, created_at, updated_at
            """
        )
        r = await db.execute(stmt, {
            "q": body.question_normalized.strip(),
            "a": body.answer_text,
            "source_type": body.source_type,
            "source_id": body.source_id,
            "provenance": json.dumps(body.provenance),
        })
        row = r.fetchone()
        await db.commit()
        return {
            "id": str(row[0]),
            "question": row[1],
            "answer_text": row[2],
            "source_type": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "updated_at": row[5].isoformat() if row[5] else None,
        }
    except Exception as e:
        await db.rollback()
        raise e
