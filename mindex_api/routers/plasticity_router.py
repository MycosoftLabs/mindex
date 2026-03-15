"""
Plasticity Forge Phase 1 — registry API (Mar 14, 2026).

MINDEX is the source of truth for model_candidate, training_run, eval_run,
promotion_decision, and runtime_alias_state. Registry-backed alias resolution
enables live alias -> candidate_id; models.yaml remains boot default.
No mock data.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, require_api_key

logger = logging.getLogger(__name__)

plasticity_router = APIRouter(
    prefix="/plasticity",
    tags=["plasticity"],
    dependencies=[Depends(require_api_key)],
)


# --- Request/response models ---

class ModelCandidateCreate(BaseModel):
    candidate_id: str = Field(..., max_length=128)
    parent_candidate_ids: List[str] = Field(default_factory=list)
    base_model_id: Optional[str] = None
    artifact_uri: Optional[str] = None
    mutation_operators_applied: List[Any] = Field(default_factory=list)  # [{operator, params}] or legacy [str]
    data_curriculum_hash: Optional[str] = None
    training_code_hash: Optional[str] = None
    eval_suite_ids: List[str] = Field(default_factory=list)
    eval_summary: Optional[Dict[str, Any]] = None
    safety_verdict: Optional[str] = None
    latency_p50_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None
    memory_mb: Optional[float] = None
    watts: Optional[float] = None
    jetson_compatible: bool = False
    lifecycle: str = Field(default="shadow", max_length=32)
    rollback_target_candidate_id: Optional[str] = None
    alias: Optional[str] = None


class ModelCandidateUpdate(BaseModel):
    eval_summary: Optional[Dict[str, Any]] = None
    safety_verdict: Optional[str] = None
    lifecycle: Optional[str] = None
    promoted_at: Optional[str] = None
    alias: Optional[str] = None


class TrainingRunCreate(BaseModel):
    run_id: str = Field(..., max_length=128)
    candidate_id: str = Field(..., max_length=128)
    nlm_run_id: Optional[str] = None
    status: str = Field(default="running", max_length=32)
    config: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)


class EvalRunCreate(BaseModel):
    eval_run_id: str = Field(..., max_length=128)
    candidate_id: str = Field(..., max_length=128)
    suite_id: str = Field(..., max_length=128)
    status: str = Field(default="running", max_length=32)
    results: Dict[str, Any] = Field(default_factory=dict)


class PromotionDecisionCreate(BaseModel):
    decision_id: str = Field(..., max_length=128)
    candidate_id: str = Field(..., max_length=128)
    from_lifecycle: str = Field(..., max_length=32)
    to_lifecycle: str = Field(..., max_length=32)
    alias: Optional[str] = None
    policy_id: Optional[str] = None
    decided_by: Optional[str] = None


class AliasSetRequest(BaseModel):
    candidate_id: str = Field(..., max_length=128)


def _row_to_candidate(row: Any) -> Dict[str, Any]:
    return {
        "candidate_id": row[0],
        "created_at": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
        "parent_candidate_ids": row[2] if isinstance(row[2], list) else (row[2] or []),
        "base_model_id": row[3],
        "artifact_uri": row[4],
        "mutation_operators_applied": row[5] if isinstance(row[5], list) else (row[5] or []),
        "data_curriculum_hash": row[6],
        "training_code_hash": row[7],
        "eval_suite_ids": row[8] if isinstance(row[8], list) else (row[8] or []),
        "eval_summary": row[9],
        "safety_verdict": row[10],
        "latency_p50_ms": row[11],
        "latency_p99_ms": row[12],
        "memory_mb": row[13],
        "watts": row[14],
        "jetson_compatible": row[15] or False,
        "lifecycle": row[16] or "shadow",
        "rollback_target_candidate_id": row[17],
        "promoted_at": row[18].isoformat() if row[18] and hasattr(row[18], "isoformat") else row[18],
        "alias": row[19],
    }


# --- Candidates ---

@plasticity_router.get("/candidates")
async def list_candidates(
    lifecycle: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """List model candidates, optionally filtered by lifecycle."""
    if lifecycle:
        stmt = text(
            """
            SELECT candidate_id, created_at, parent_candidate_ids, base_model_id, artifact_uri,
                   mutation_operators_applied, data_curriculum_hash, training_code_hash,
                   eval_suite_ids, eval_summary, safety_verdict, latency_p50_ms, latency_p99_ms,
                   memory_mb, watts, jetson_compatible, lifecycle, rollback_target_candidate_id,
                   promoted_at, alias
            FROM plasticity.model_candidate
            WHERE lifecycle = :lifecycle
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """
        )
        result = await db.execute(stmt, {"lifecycle": lifecycle, "limit": limit, "offset": offset})
    else:
        stmt = text(
            """
            SELECT candidate_id, created_at, parent_candidate_ids, base_model_id, artifact_uri,
                   mutation_operators_applied, data_curriculum_hash, training_code_hash,
                   eval_suite_ids, eval_summary, safety_verdict, latency_p50_ms, latency_p99_ms,
                   memory_mb, watts, jetson_compatible, lifecycle, rollback_target_candidate_id,
                   promoted_at, alias
            FROM plasticity.model_candidate
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """
        )
        result = await db.execute(stmt, {"limit": limit, "offset": offset})
    rows = result.fetchall()
    return {"candidates": [_row_to_candidate(r) for r in rows]}


@plasticity_router.post("/candidates")
async def create_candidate(
    body: ModelCandidateCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Create a model candidate (genome) record."""
    try:
        stmt = text(
            """
            INSERT INTO plasticity.model_candidate (
                candidate_id, parent_candidate_ids, base_model_id, artifact_uri,
                mutation_operators_applied, data_curriculum_hash, training_code_hash,
                eval_suite_ids, eval_summary, safety_verdict, latency_p50_ms, latency_p99_ms,
                memory_mb, watts, jetson_compatible, lifecycle, rollback_target_candidate_id, alias
            ) VALUES (
                :candidate_id, :parent_candidate_ids::jsonb, :base_model_id, :artifact_uri,
                :mutation_operators_applied::jsonb, :data_curriculum_hash, :training_code_hash,
                :eval_suite_ids::jsonb, :eval_summary::jsonb, :safety_verdict,
                :latency_p50_ms, :latency_p99_ms, :memory_mb, :watts, :jetson_compatible,
                :lifecycle, :rollback_target_candidate_id, :alias
            )
            RETURNING candidate_id, created_at
            """
        )
        result = await db.execute(
            stmt,
            {
                "candidate_id": body.candidate_id,
                "parent_candidate_ids": json.dumps(body.parent_candidate_ids),
                "base_model_id": body.base_model_id,
                "artifact_uri": body.artifact_uri,
                "mutation_operators_applied": json.dumps(body.mutation_operators_applied),
                "data_curriculum_hash": body.data_curriculum_hash,
                "training_code_hash": body.training_code_hash,
                "eval_suite_ids": json.dumps(body.eval_suite_ids),
                "eval_summary": json.dumps(body.eval_summary) if body.eval_summary else None,
                "safety_verdict": body.safety_verdict,
                "latency_p50_ms": body.latency_p50_ms,
                "latency_p99_ms": body.latency_p99_ms,
                "memory_mb": body.memory_mb,
                "watts": body.watts,
                "jetson_compatible": body.jetson_compatible,
                "lifecycle": body.lifecycle,
                "rollback_target_candidate_id": body.rollback_target_candidate_id,
                "alias": body.alias,
            },
        )
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Insert did not return row")
        return {"candidate_id": row[0], "created_at": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1])}
    except Exception as e:
        await db.rollback()
        logger.exception("Create candidate failed")
        raise HTTPException(status_code=500, detail=f"create_failed: {e!s}") from e


@plasticity_router.get("/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Get one model candidate by id."""
    stmt = text(
        """
        SELECT candidate_id, created_at, parent_candidate_ids, base_model_id, artifact_uri,
               mutation_operators_applied, data_curriculum_hash, training_code_hash,
               eval_suite_ids, eval_summary, safety_verdict, latency_p50_ms, latency_p99_ms,
               memory_mb, watts, jetson_compatible, lifecycle, rollback_target_candidate_id,
               promoted_at, alias
        FROM plasticity.model_candidate
        WHERE candidate_id = :candidate_id
        """
    )
    result = await db.execute(stmt, {"candidate_id": candidate_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="candidate_not_found")
    return _row_to_candidate(row)


@plasticity_router.put("/candidates/{candidate_id}")
async def update_candidate(
    candidate_id: str,
    body: ModelCandidateUpdate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Update candidate (eval_summary, safety_verdict, lifecycle, promoted_at, alias)."""
    updates = []
    params: Dict[str, Any] = {"candidate_id": candidate_id}
    if body.eval_summary is not None:
        updates.append("eval_summary = :eval_summary::jsonb")
        params["eval_summary"] = json.dumps(body.eval_summary)
    if body.safety_verdict is not None:
        updates.append("safety_verdict = :safety_verdict")
        params["safety_verdict"] = body.safety_verdict
    if body.lifecycle is not None:
        updates.append("lifecycle = :lifecycle")
        params["lifecycle"] = body.lifecycle
    if body.promoted_at is not None:
        updates.append("promoted_at = :promoted_at::timestamptz")
        params["promoted_at"] = body.promoted_at
    if body.alias is not None:
        updates.append("alias = :alias")
        params["alias"] = body.alias
    if not updates:
        return {"updated": 0}
    stmt = text(
        f"UPDATE plasticity.model_candidate SET {', '.join(updates)} WHERE candidate_id = :candidate_id"
    )
    result = await db.execute(stmt, params)
    await db.commit()
    return {"updated": result.rowcount or 0}


# --- Runtime alias (registry-backed resolver) ---

@plasticity_router.get("/aliases")
async def list_aliases(db: AsyncSession = Depends(get_db_session)) -> Dict[str, str]:
    """List all alias -> candidate_id (for resolver)."""
    stmt = text(
        "SELECT alias, candidate_id FROM plasticity.runtime_alias_state"
    )
    result = await db.execute(stmt)
    rows = result.fetchall()
    return {r[0]: r[1] for r in rows}


@plasticity_router.get("/aliases/{alias}")
async def resolve_alias(
    alias: str,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Resolve alias to candidate_id. Used by MAS registry-backed resolver; fallback to models.yaml when missing."""
    stmt = text(
        "SELECT alias, candidate_id, updated_at FROM plasticity.runtime_alias_state WHERE alias = :alias"
    )
    result = await db.execute(stmt, {"alias": alias})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="alias_not_found")
    return {
        "alias": row[0],
        "candidate_id": row[1],
        "updated_at": row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2]),
    }


@plasticity_router.put("/aliases/{alias}")
async def set_alias(
    alias: str,
    body: AliasSetRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Set alias -> candidate_id (upsert). Promotion controller uses this."""
    try:
        stmt = text(
            """
            INSERT INTO plasticity.runtime_alias_state (alias, candidate_id)
            VALUES (:alias, :candidate_id)
            ON CONFLICT (alias) DO UPDATE SET candidate_id = EXCLUDED.candidate_id, updated_at = NOW()
            RETURNING alias, candidate_id, updated_at
            """
        )
        result = await db.execute(stmt, {"alias": alias, "candidate_id": body.candidate_id})
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Upsert did not return row")
        return {
            "alias": row[0],
            "candidate_id": row[1],
            "updated_at": row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2]),
        }
    except Exception as e:
        await db.rollback()
        logger.exception("Set alias failed")
        raise HTTPException(status_code=500, detail=f"set_alias_failed: {e!s}") from e


# --- Training runs ---

@plasticity_router.post("/training-runs")
async def create_training_run(
    body: TrainingRunCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Create a training run linked to a candidate."""
    try:
        stmt = text(
            """
            INSERT INTO plasticity.training_run (run_id, candidate_id, nlm_run_id, status, config, metrics)
            VALUES (:run_id, :candidate_id, :nlm_run_id, :status, :config::jsonb, :metrics::jsonb)
            RETURNING run_id, candidate_id, started_at
            """
        )
        result = await db.execute(
            stmt,
            {
                "run_id": body.run_id,
                "candidate_id": body.candidate_id,
                "nlm_run_id": body.nlm_run_id,
                "status": body.status,
                "config": json.dumps(body.config),
                "metrics": json.dumps(body.metrics),
            },
        )
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Insert did not return row")
        return {"run_id": row[0], "candidate_id": row[1], "started_at": row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2])}
    except Exception as e:
        await db.rollback()
        if "foreign key" in str(e).lower() or "violates" in str(e).lower():
            raise HTTPException(status_code=400, detail="candidate_id not found") from e
        logger.exception("Create training run failed")
        raise HTTPException(status_code=500, detail=f"create_failed: {e!s}") from e


# --- Eval runs ---

@plasticity_router.post("/eval-runs")
async def create_eval_run(
    body: EvalRunCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Create an eval run for a candidate."""
    try:
        stmt = text(
            """
            INSERT INTO plasticity.eval_run (eval_run_id, candidate_id, suite_id, status, results)
            VALUES (:eval_run_id, :candidate_id, :suite_id, :status, :results::jsonb)
            RETURNING eval_run_id, candidate_id, started_at
            """
        )
        result = await db.execute(
            stmt,
            {
                "eval_run_id": body.eval_run_id,
                "candidate_id": body.candidate_id,
                "suite_id": body.suite_id,
                "status": body.status,
                "results": json.dumps(body.results),
            },
        )
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Insert did not return row")
        return {"eval_run_id": row[0], "candidate_id": row[1], "started_at": row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2])}
    except Exception as e:
        await db.rollback()
        if "foreign key" in str(e).lower() or "violates" in str(e).lower():
            raise HTTPException(status_code=400, detail="candidate_id not found") from e
        logger.exception("Create eval run failed")
        raise HTTPException(status_code=500, detail=f"create_failed: {e!s}") from e


# --- Promotion decisions ---

@plasticity_router.post("/promotion-decisions")
async def create_promotion_decision(
    body: PromotionDecisionCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Record a promotion decision (shadow/canary -> active or rollback)."""
    try:
        stmt = text(
            """
            INSERT INTO plasticity.promotion_decision (
                decision_id, candidate_id, from_lifecycle, to_lifecycle, alias, policy_id, decided_by
            ) VALUES (:decision_id, :candidate_id, :from_lifecycle, :to_lifecycle, :alias, :policy_id, :decided_by)
            RETURNING decision_id, decided_at
            """
        )
        result = await db.execute(
            stmt,
            {
                "decision_id": body.decision_id,
                "candidate_id": body.candidate_id,
                "from_lifecycle": body.from_lifecycle,
                "to_lifecycle": body.to_lifecycle,
                "alias": body.alias,
                "policy_id": body.policy_id,
                "decided_by": body.decided_by,
            },
        )
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Insert did not return row")
        return {"decision_id": row[0], "decided_at": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1])}
    except Exception as e:
        await db.rollback()
        if "foreign key" in str(e).lower() or "violates" in str(e).lower():
            raise HTTPException(status_code=400, detail="candidate_id not found") from e
        logger.exception("Create promotion decision failed")
        raise HTTPException(status_code=500, detail=f"create_failed: {e!s}") from e
