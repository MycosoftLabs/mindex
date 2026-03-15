"""
NLM persistence router - March 14, 2026

Plasticity Forge Phase 1: MINDEX is the persistence authority for NLM training,
evals, lineage, and NMF (Nature Message Frame) records. This router stores
NMFs produced by the NLM translation layer (raw -> normalized -> bio-tokens -> NMF).
No mock data; real persistence only.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, require_api_key

logger = logging.getLogger(__name__)

nlm_router = APIRouter(
    prefix="/nlm",
    tags=["nlm"],
    dependencies=[Depends(require_api_key)],
)


class NMFPersistRequest(BaseModel):
    """Request body for persisting a Nature Message Frame (NMF)."""
    packet: Dict[str, Any] = Field(..., description="Full NMF as JSON (from NLM translate)")
    source_id: str = Field(default="", max_length=128, description="Source/device identifier")
    anomaly_score: float = Field(default=0.0, ge=0.0, description="Anomaly score if available")


@nlm_router.post("/nmf")
async def persist_nmf(
    req: NMFPersistRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Persist a Nature Message Frame to MINDEX.

    NLM service calls this after translating raw telemetry to NMF
    (raw -> normalized -> bio-tokens -> NMF). Stores in nlm.nature_embeddings
    with packet JSONB; embedding column left NULL until embedding pipeline exists.
    """
    try:
        stmt = text(
            """
            INSERT INTO nlm.nature_embeddings (source_id, packet, anomaly_score)
            VALUES (:source_id, :packet::jsonb, :anomaly_score)
            RETURNING embedding_id, ts
            """
        )
        import json
        packet_json = json.dumps(req.packet)
        result = await db.execute(
            stmt,
            {
                "source_id": req.source_id or "",
                "packet": packet_json,
                "anomaly_score": req.anomaly_score,
            },
        )
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Insert did not return row")
        return {
            "success": True,
            "embedding_id": row[0],
            "ts": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
        }
    except Exception as e:
        await db.rollback()
        logger.exception("NMF persist failed")
        raise HTTPException(status_code=500, detail=f"persist_failed: {e!s}") from e
