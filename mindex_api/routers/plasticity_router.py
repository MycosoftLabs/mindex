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
        prev = await db.execute(
            text("SELECT candidate_id FROM plasticity.runtime_alias_state WHERE alias = :alias"),
            {"alias": alias},
        )
        prow = prev.fetchone()
        from_cid = prow[0] if prow else None
        stmt = text(
            """
            INSERT INTO plasticity.runtime_alias_state (alias, candidate_id)
            VALUES (:alias, :candidate_id)
            ON CONFLICT (alias) DO UPDATE SET candidate_id = EXCLUDED.candidate_id, updated_at = NOW()
            RETURNING alias, candidate_id, updated_at
            """
        )
        result = await db.execute(stmt, {"alias": alias, "candidate_id": body.candidate_id})
        await db.execute(text("SAVEPOINT alias_hist_sp"))
        try:
            await db.execute(
                text(
                    """
                    INSERT INTO plasticity.alias_history (alias, from_candidate_id, to_candidate_id, reason)
                    VALUES (:alias, :from_c, :to_c, :reason)
                    """
                ),
                {
                    "alias": alias,
                    "from_c": from_cid,
                    "to_c": body.candidate_id,
                    "reason": "alias_upsert",
                },
            )
            await db.execute(text("RELEASE SAVEPOINT alias_hist_sp"))
        except Exception:
            await db.execute(text("ROLLBACK TO SAVEPOINT alias_hist_sp"))
            logger.debug("alias_history insert skipped or table missing")
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


# --- MYCA2 PSILO sessions (Mar 17, 2026) ---


class PsiloSessionCreate(BaseModel):
    session_id: Optional[str] = None
    dose_profile: Dict[str, Any] = Field(default_factory=dict)
    phase_profile: Dict[str, Any] = Field(default_factory=dict)


class PsiloSessionPatch(BaseModel):
    status: Optional[str] = None
    overlay_edges: Optional[List[Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    integration_report: Optional[Dict[str, Any]] = None
    dose_profile: Optional[Dict[str, Any]] = None
    phase_profile: Optional[Dict[str, Any]] = None


class PsiloEventCreate(BaseModel):
    event_type: str = Field(..., max_length=128)
    payload: Dict[str, Any] = Field(default_factory=dict)


@plasticity_router.post("/psilo/sessions")
async def psilo_session_create(
    body: PsiloSessionCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    import uuid

    sid = (body.session_id or "").strip() or f"psilo_{uuid.uuid4().hex[:16]}"
    try:
        await db.execute(
            text(
                """
                INSERT INTO plasticity.psilo_session (session_id, dose_profile, phase_profile, status)
                VALUES (:sid, :dose::jsonb, :phase::jsonb, 'active')
                """
            ),
            {"sid": sid, "dose": json.dumps(body.dose_profile), "phase": json.dumps(body.phase_profile)},
        )
        await db.commit()
        return {"session_id": sid, "status": "active"}
    except Exception as e:
        await db.rollback()
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(status_code=409, detail="session_id exists") from e
        logger.exception("psilo session create")
        raise HTTPException(status_code=500, detail=str(e)) from e


@plasticity_router.get("/psilo/sessions/{session_id}")
async def psilo_session_get(session_id: str, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    r = await db.execute(
        text(
            """
            SELECT session_id, status, dose_profile, phase_profile, overlay_edges, metrics,
                   started_at, ended_at, integration_report
            FROM plasticity.psilo_session WHERE session_id = :sid
            """
        ),
        {"sid": session_id},
    )
    row = r.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": row[0],
        "status": row[1],
        "dose_profile": row[2] if isinstance(row[2], dict) else (row[2] or {}),
        "phase_profile": row[3] if isinstance(row[3], dict) else (row[3] or {}),
        "overlay_edges": row[4] if isinstance(row[4], list) else (row[4] or []),
        "metrics": row[5] if isinstance(row[5], dict) else (row[5] or {}),
        "started_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
        "ended_at": row[7].isoformat() if row[7] and hasattr(row[7], "isoformat") else row[7],
        "integration_report": row[8],
    }


@plasticity_router.patch("/psilo/sessions/{session_id}")
async def psilo_session_patch(
    session_id: str,
    body: PsiloSessionPatch,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    updates: List[str] = []
    params: Dict[str, Any] = {"sid": session_id}
    if body.status is not None:
        updates.append("status = :status")
        params["status"] = body.status
        if body.status in ("stopped", "killed", "ended"):
            updates.append("ended_at = NOW()")
    if body.overlay_edges is not None:
        updates.append("overlay_edges = :edges::jsonb")
        params["edges"] = json.dumps(body.overlay_edges)
    if body.metrics is not None:
        updates.append("metrics = :metrics::jsonb")
        params["metrics"] = json.dumps(body.metrics)
    if body.integration_report is not None:
        updates.append("integration_report = :ir::jsonb")
        params["ir"] = json.dumps(body.integration_report)
    if body.dose_profile is not None:
        updates.append("dose_profile = :dose::jsonb")
        params["dose"] = json.dumps(body.dose_profile)
    if body.phase_profile is not None:
        updates.append("phase_profile = :phase::jsonb")
        params["phase"] = json.dumps(body.phase_profile)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        await db.execute(
            text(f"UPDATE plasticity.psilo_session SET {', '.join(updates)} WHERE session_id = :sid"),
            params,
        )
        await db.commit()
        return {"session_id": session_id, "updated": True}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


@plasticity_router.post("/psilo/sessions/{session_id}/events")
async def psilo_session_append_event(
    session_id: str,
    body: PsiloEventCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    try:
        await db.execute(
            text(
                """
                INSERT INTO plasticity.psilo_session_event (session_id, event_type, payload)
                VALUES (:sid, :et, :pl::jsonb)
                RETURNING id, created_at
                """
            ),
            {"sid": session_id, "et": body.event_type, "pl": json.dumps(body.payload)},
        )
        await db.commit()
        return {"session_id": session_id, "event_type": body.event_type, "ok": True}
    except Exception as e:
        await db.rollback()
        if "foreign key" in str(e).lower():
            raise HTTPException(status_code=404, detail="session not found") from e
        raise HTTPException(status_code=500, detail=str(e)) from e


@plasticity_router.get("/psilo/sessions/{session_id}/events")
async def psilo_session_list_events(
    session_id: str,
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    r = await db.execute(
        text(
            """
            SELECT id, event_type, payload, created_at
            FROM plasticity.psilo_session_event
            WHERE session_id = :sid ORDER BY id DESC LIMIT :lim
            """
        ),
        {"sid": session_id, "lim": limit},
    )
    rows = r.fetchall()
    return {
        "session_id": session_id,
        "events": [
            {
                "id": x[0],
                "event_type": x[1],
                "payload": x[2] if isinstance(x[2], dict) else {},
                "created_at": x[3].isoformat() if hasattr(x[3], "isoformat") else str(x[3]),
            }
            for x in rows
        ],
    }


class MutationRunCreate(BaseModel):
    mutation_run_id: str
    candidate_id: Optional[str] = None
    parent_mutation_run_id: Optional[str] = None
    operators_applied: List[Any] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)


@plasticity_router.post("/mutation-runs")
async def create_mutation_run(body: MutationRunCreate, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    await db.execute(
        text(
            """
            INSERT INTO plasticity.mutation_run (mutation_run_id, candidate_id, parent_mutation_run_id, operators_applied, status, config)
            VALUES (:id, :cid, :pid, :ops::jsonb, 'running', :cfg::jsonb)
            """
        ),
        {
            "id": body.mutation_run_id,
            "cid": body.candidate_id,
            "pid": body.parent_mutation_run_id,
            "ops": json.dumps(body.operators_applied),
            "cfg": json.dumps(body.config),
        },
    )
    await db.commit()
    return {"mutation_run_id": body.mutation_run_id, "status": "running"}


class LineageEventCreate(BaseModel):
    event_id: str
    candidate_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)


@plasticity_router.post("/lineage-events")
async def create_lineage_event(body: LineageEventCreate, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    await db.execute(
        text(
            """
            INSERT INTO plasticity.lineage_event (event_id, candidate_id, event_type, payload)
            VALUES (:eid, :cid, :et, :pl::jsonb)
            """
        ),
        {"eid": body.event_id, "cid": body.candidate_id, "et": body.event_type, "pl": json.dumps(body.payload)},
    )
    await db.commit()
    return {"event_id": body.event_id}


class EvalCaseResultCreate(BaseModel):
    eval_run_id: str
    case_id: str
    passed: Optional[bool] = None
    score: Optional[float] = None
    details: Dict[str, Any] = Field(default_factory=dict)


@plasticity_router.post("/eval-case-results")
async def create_eval_case_result(body: EvalCaseResultCreate, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    try:
        r = await db.execute(
            text(
                """
                INSERT INTO plasticity.eval_case_result (eval_run_id, case_id, passed, score, details)
                VALUES (:eid, :cid, :p, :s, :d::jsonb) RETURNING id
                """
            ),
            {
                "eid": body.eval_run_id,
                "cid": body.case_id,
                "p": body.passed,
                "s": body.score,
                "d": json.dumps(body.details),
            },
        )
        await db.commit()
        row = r.fetchone()
        return {"id": row[0] if row else None}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


class ArtifactMetaCreate(BaseModel):
    artifact_id: str
    candidate_id: Optional[str] = None
    uri: str
    content_hash: Optional[str] = None
    kind: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


@plasticity_router.post("/artifact-meta")
async def create_artifact_meta(body: ArtifactMetaCreate, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    await db.execute(
        text(
            """
            INSERT INTO plasticity.artifact_meta (artifact_id, candidate_id, uri, content_hash, kind, meta)
            VALUES (:aid, :cid, :uri, :h, :k, :m::jsonb)
            """
        ),
        {
            "aid": body.artifact_id,
            "cid": body.candidate_id,
            "uri": body.uri,
            "h": body.content_hash,
            "k": body.kind,
            "m": json.dumps(body.meta),
        },
    )
    await db.commit()
    return {"artifact_id": body.artifact_id}


class RollbackEventCreate(BaseModel):
    rollback_id: str
    alias: str
    from_candidate_id: Optional[str] = None
    to_candidate_id: str
    decided_by: Optional[str] = None


@plasticity_router.post("/rollback-events")
async def create_rollback_event(body: RollbackEventCreate, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    await db.execute(
        text(
            """
            INSERT INTO plasticity.rollback_event (rollback_id, alias, from_candidate_id, to_candidate_id, decided_by)
            VALUES (:rid, :al, :fc, :tc, :db)
            """
        ),
        {
            "rid": body.rollback_id,
            "al": body.alias,
            "fc": body.from_candidate_id,
            "tc": body.to_candidate_id,
            "db": body.decided_by,
        },
    )
    await db.commit()
    return {"rollback_id": body.rollback_id}


@plasticity_router.get("/alias-history")
async def list_alias_history(
    alias: Optional[str] = None,
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    if alias:
        r = await db.execute(
            text(
                """
                SELECT id, alias, from_candidate_id, to_candidate_id, changed_at, reason
                FROM plasticity.alias_history WHERE alias = :a ORDER BY id DESC LIMIT :lim
                """
            ),
            {"a": alias, "lim": limit},
        )
    else:
        r = await db.execute(
            text(
                """
                SELECT id, alias, from_candidate_id, to_candidate_id, changed_at, reason
                FROM plasticity.alias_history ORDER BY id DESC LIMIT :lim
                """
            ),
            {"lim": limit},
        )
    rows = r.fetchall()
    return {
        "items": [
            {
                "id": x[0],
                "alias": x[1],
                "from_candidate_id": x[2],
                "to_candidate_id": x[3],
                "changed_at": x[4].isoformat() if hasattr(x[4], "isoformat") else str(x[4]),
                "reason": x[5],
            }
            for x in rows
        ]
    }
