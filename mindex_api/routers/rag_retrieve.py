"""RAG retrieval via unified MINDEX search (keyword path; embeddings may extend later)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from .unified_search import ALL_DOMAINS, _build_dispatch, _resolve_domains

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["RAG Retrieval"])


DEFAULT_RAG_TYPES = (
    "research,taxa,species,compounds,genetics,observations,crep_entities"
)


class RAGRetrieveRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=4000)
    limit: int = Field(10, ge=1, le=50, description="Max rows per domain before flattening")
    types: str = Field(
        DEFAULT_RAG_TYPES,
        description="Comma-separated unified-search domains or groups (see unified-search)",
    )
    embedding_model_id: Optional[str] = None
    world_state_ref: Optional[dict[str, Any]] = None


class RetrievedChunk(BaseModel):
    content: str
    source_id: str
    collection: str
    timestamp: Optional[str] = None
    score: float = 1.0
    provenance_root: str = "mindex_unified_search"
    world_state_ref: Optional[dict[str, Any]] = None


class RAGRetrieveResponse(BaseModel):
    query: str
    chunks: list[RetrievedChunk]
    retrieval_mode: str = "keyword_unified"
    domains_searched: list[str]
    timing_ms: int
    total_chunks: int


def _row_to_content(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "name",
        "title",
        "abstract",
        "entity_type",
        "domain",
        "doi",
        "journal",
    ):
        val = row.get(key)
        if val is not None and str(val).strip():
            parts.append(str(val).strip())
    if parts:
        return " | ".join(parts)[:8000]
    try:
        return json.dumps(row, default=str)[:8000]
    except Exception:
        return str(row)[:8000]


def _flatten_results(
    results_by_domain: dict[str, list],
    per_domain_cap: int,
) -> list[RetrievedChunk]:
    chunks: list[RetrievedChunk] = []
    for domain, rows in results_by_domain.items():
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows[:per_domain_cap]):
            if not isinstance(row, dict):
                continue
            rid = row.get("id")
            source_id = str(rid) if rid is not None else f"{domain}_{i}"
            ts = row.get("occurred_at") or row.get("year")
            if ts is not None:
                ts = str(ts)
            chunks.append(
                RetrievedChunk(
                    content=_row_to_content(row),
                    source_id=source_id,
                    collection=domain,
                    timestamp=ts,
                    score=1.0,
                    provenance_root="mindex_unified_search",
                    world_state_ref=None,
                )
            )
    return chunks


@router.post("/retrieve", response_model=RAGRetrieveResponse)
async def rag_retrieve(
    body: RAGRetrieveRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """Return chunks from unified search (keyword path)."""
    start = time.time()
    domains = _resolve_domains(body.types)
    if not domains:
        domains = list(ALL_DOMAINS)

    dispatch = _build_dispatch(
        session,
        body.query,
        body.limit,
        None,
        None,
        100.0,
        None,
        None,
        None,
    )

    tasks = []
    names: list[str] = []
    for d in domains:
        if d in dispatch:
            tasks.append(dispatch[d])
            names.append(d)

    if not tasks:
        timing_ms = int((time.time() - start) * 1000)
        return RAGRetrieveResponse(
            query=body.query,
            chunks=[],
            retrieval_mode="keyword_unified",
            domains_searched=[],
            timing_ms=timing_ms,
            total_chunks=0,
        )

    raw = await asyncio.gather(*tasks, return_exceptions=True)
    results: dict[str, list] = {}
    for name, result in zip(names, raw):
        if isinstance(result, Exception):
            logger.warning("RAG domain %s failed: %s", name, result)
            results[name] = []
        else:
            results[name] = result if isinstance(result, list) else []

    chunks = _flatten_results(results, body.limit)
    timing_ms = int((time.time() - start) * 1000)

    return RAGRetrieveResponse(
        query=body.query,
        chunks=chunks,
        retrieval_mode="keyword_unified",
        domains_searched=names,
        timing_ms=timing_ms,
        total_chunks=len(chunks),
    )
