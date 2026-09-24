"""NLM and TAC-O inference router for MINDEX."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

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
    packet: Dict[str, Any] = Field(..., description="Full NMF as JSON (from NLM translate)")
    source_id: str = Field(default="", max_length=128)
    anomaly_score: float = Field(default=0.0, ge=0.0)


@nlm_router.post("/nmf")
async def persist_nmf(
    req: NMFPersistRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    try:
        stmt = text(
            """
            INSERT INTO nlm.nature_embeddings (source_id, packet, anomaly_score)
            VALUES (:source_id, CAST(:packet AS jsonb), :anomaly_score)
            RETURNING embedding_id, ts
            """
        )
        result = await db.execute(
            stmt,
            {
                "source_id": req.source_id,
                "packet": json.dumps(req.packet),
                "anomaly_score": req.anomaly_score,
            },
        )
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="insert_failed")
        return {
            "success": True,
            "embedding_id": row[0],
            "ts": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
        }
    except Exception as exc:
        await db.rollback()
        logger.exception("NMF persist failed")
        raise HTTPException(status_code=500, detail=f"persist_failed: {exc!s}") from exc


@nlm_router.get("/nmf/{embedding_id}")
async def get_nmf(
    embedding_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    try:
        stmt = text(
            """
            SELECT embedding_id, source_id, packet, anomaly_score, ts
            FROM nlm.nature_embeddings
            WHERE embedding_id = CAST(:embedding_id AS bigint)
            """
        )
        result = await db.execute(stmt, {"embedding_id": embedding_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="embedding_not_found")
        packet = row[2]
        if hasattr(packet, "keys"):
            packet_out = dict(packet)
        else:
            packet_out = json.loads(packet) if isinstance(packet, str) else packet
        return {
            "success": True,
            "embedding_id": str(row[0]),
            "source_id": row[1],
            "packet": packet_out,
            "anomaly_score": float(row[3] or 0.0),
            "ts": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("NMF get failed")
        raise HTTPException(status_code=500, detail=f"get_failed: {exc!s}") from exc


def _pick_classification(payload: Dict[str, Any]) -> Dict[str, Any]:
    cavitation = float(payload.get("cavitation_index", 0.0) or 0.0)
    broadband = float(payload.get("broadband_level_db", 0.0) or 0.0)
    modulation = float(payload.get("modulation_rate_hz", 0.0) or 0.0)
    marine_mammal_score = min(max(float(payload.get("marine_mammal_score", 0.0) or 0.0), 0.0), 1.0)

    if marine_mammal_score >= 0.8:
        label = "marine_mammal"
    elif cavitation >= 0.75 and broadband >= 125:
        label = "submarine"
    elif modulation >= 25 and broadband >= 110:
        label = "torpedo"
    elif broadband >= 90:
        label = "surface_vessel"
    else:
        label = "ambient"

    confidence = min(max((0.4 + cavitation * 0.3 + (broadband / 200.0) * 0.3), 0.0), 1.0)
    avani_action = "veto" if label == "marine_mammal" else "pass"

    return {
        "classification": label,
        "confidence": round(confidence, 3),
        "marine_mammal_score": marine_mammal_score,
        "avani_action": avani_action,
        "recommendation": "Reduce active sonar power" if label == "marine_mammal" else "Track and classify contact",
    }


@nlm_router.post("/classify/acoustic")
async def classify_acoustic(payload: Dict[str, Any]):
    result = _pick_classification(payload)
    return {
        "status": "ok",
        **result,
        "categories": [
            "submarine",
            "surface_vessel",
            "torpedo",
            "uuv",
            "mine",
            "marine_mammal",
            "fish_school",
            "seismic",
            "weather_noise",
            "shipping_noise",
            "ambient",
            "unknown",
        ],
    }


@nlm_router.post("/predict/sonar-performance")
async def predict_sonar_performance(payload: Dict[str, Any]):
    sound_speed = float(payload.get("sound_speed", 1500) or 1500)
    sea_state = float(payload.get("sea_state", 3) or 3)
    noise = float(payload.get("ambient_noise_level_db", 75) or 75)

    quality = max(0.05, min(1.0, 1.15 - (sea_state * 0.1) - (noise / 200.0)))
    max_range = int(12000 * quality)
    min_range = int(max_range * 0.22)
    optimal_depth = float(payload.get("thermocline_depth_m", 80) or 80)

    return {
        "status": "ok",
        "min_range_m": min_range,
        "max_range_m": max_range,
        "optimal_depth_m": optimal_depth,
        "figure_of_merit_db": round((sound_speed / 100.0) * quality, 2),
        "confidence": round(quality, 3),
        "environmental_factors": {
            "sound_speed": sound_speed,
            "sea_state": sea_state,
            "ambient_noise_level_db": noise,
        },
    }


@nlm_router.post("/assess/tactical")
async def tactical_assessment(payload: Dict[str, Any]):
    urgency = float(payload.get("urgency", 0.5) or 0.5)
    urgency = max(0.0, min(urgency, 1.0))

    if urgency >= 0.8:
        recommendation = "Alert operator and deploy additional passive nodes"
    elif urgency >= 0.5:
        recommendation = "Reposition sensors and increase classification cadence"
    else:
        recommendation = "Log and continue passive monitoring"

    return {
        "status": "ok",
        "recommendation": recommendation,
        "urgency": urgency,
        "available_actions": [
            "reposition_sensors",
            "increase_gain",
            "decrease_gain",
            "deploy_deep",
            "deploy_shallow",
            "activate_magnetic",
            "classify_contact",
            "alert_operator",
            "log_and_continue",
            "request_verification",
        ],
    }


class TrainingRunUpsert(BaseModel):
    run_id: str = Field(..., max_length=64)
    status: str = Field(default="running", max_length=32)
    config: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    formspace_chart_ids: List[Any] = Field(default_factory=list)
    merkle_root: Optional[str] = None
    sha256_leaf: Optional[str] = None
    ecdsa_signature: Optional[str] = None
    storage_ref: Optional[str] = None


@nlm_router.post("/training/runs")
async def upsert_training_run(
    req: TrainingRunUpsert,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Upsert NLM training run (SQL mirror of MAS NAS store)."""
    try:
        stmt = text(
            """
            INSERT INTO nlm.training_runs (
                run_id, status, config, metrics, formspace_chart_ids,
                merkle_root, sha256_leaf, ecdsa_signature, storage_ref, updated_at
            )
            VALUES (
                :run_id, :status, CAST(:config AS jsonb), CAST(:metrics AS jsonb),
                CAST(:formspace_chart_ids AS jsonb),
                :merkle_root, :sha256_leaf, :ecdsa_signature, :storage_ref, NOW()
            )
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status,
                config = EXCLUDED.config,
                metrics = EXCLUDED.metrics,
                formspace_chart_ids = EXCLUDED.formspace_chart_ids,
                merkle_root = COALESCE(EXCLUDED.merkle_root, nlm.training_runs.merkle_root),
                sha256_leaf = COALESCE(EXCLUDED.sha256_leaf, nlm.training_runs.sha256_leaf),
                ecdsa_signature = COALESCE(EXCLUDED.ecdsa_signature, nlm.training_runs.ecdsa_signature),
                storage_ref = COALESCE(EXCLUDED.storage_ref, nlm.training_runs.storage_ref),
                updated_at = NOW()
            RETURNING run_id, status, started_at, updated_at
            """
        )
        result = await db.execute(
            stmt,
            {
                "run_id": req.run_id,
                "status": req.status,
                "config": json.dumps(req.config),
                "metrics": json.dumps(req.metrics),
                "formspace_chart_ids": json.dumps(req.formspace_chart_ids),
                "merkle_root": req.merkle_root,
                "sha256_leaf": req.sha256_leaf,
                "ecdsa_signature": req.ecdsa_signature,
                "storage_ref": req.storage_ref,
            },
        )
        await db.commit()
        row = result.fetchone()
        return {
            "success": True,
            "run_id": row[0] if row else req.run_id,
            "status": row[1] if row else req.status,
            "started_at": row[2].isoformat() if row and hasattr(row[2], "isoformat") else None,
            "updated_at": row[3].isoformat() if row and hasattr(row[3], "isoformat") else None,
            "model_kind": "nature_learning_model",
        }
    except Exception as exc:
        await db.rollback()
        logger.exception("training run upsert failed")
        raise HTTPException(status_code=500, detail=f"upsert_failed: {exc!s}") from exc


@nlm_router.get("/training/runs")
async def list_training_runs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    try:
        stmt = text(
            """
            SELECT run_id, status, config, metrics, formspace_chart_ids,
                   merkle_root, sha256_leaf, storage_ref, started_at, completed_at, updated_at
            FROM nlm.training_runs
            ORDER BY COALESCE(updated_at, started_at) DESC
            LIMIT :limit
            """
        )
        result = await db.execute(stmt, {"limit": max(1, min(limit, 500))})
        rows = result.fetchall()
        runs = []
        for r in rows:
            runs.append(
                {
                    "run_id": r[0],
                    "status": r[1],
                    "config": dict(r[2]) if hasattr(r[2], "keys") else (json.loads(r[2]) if isinstance(r[2], str) else r[2]),
                    "metrics": dict(r[3]) if hasattr(r[3], "keys") else (json.loads(r[3]) if isinstance(r[3], str) else r[3]),
                    "formspace_chart_ids": r[4] if r[4] is not None else [],
                    "merkle_root": r[5],
                    "sha256_leaf": r[6],
                    "storage_ref": r[7],
                    "started_at": r[8].isoformat() if r[8] and hasattr(r[8], "isoformat") else r[8],
                    "completed_at": r[9].isoformat() if r[9] and hasattr(r[9], "isoformat") else r[9],
                    "updated_at": r[10].isoformat() if r[10] and hasattr(r[10], "isoformat") else r[10],
                    "model_kind": "nature_learning_model",
                }
            )
        return {
            "success": True,
            "empty": len(runs) == 0,
            "count": len(runs),
            "runs": runs,
        }
    except Exception as exc:
        logger.exception("list training runs failed")
        # Table may not exist yet — honest empty, not mock
        return {
            "success": False,
            "empty": True,
            "count": 0,
            "runs": [],
            "error": str(exc),
            "message": "Apply migration 0040_nlm_formspace_spine_SEP23_2026.sql",
        }


class RootedFrameUpsert(BaseModel):
    frame_id: str = Field(..., max_length=64)
    device_id: str = Field(..., max_length=128)
    sensor_id: Optional[str] = Field(default=None, max_length=128)
    protocol: Dict[str, Any] = Field(default_factory=dict)
    sha256: str = Field(..., min_length=64, max_length=64)
    merkle_root: Optional[str] = None
    storage_ref: str
    signed: bool = False
    source: str = "nlm_ingest"
    labels: Dict[str, Any] = Field(default_factory=dict)


@nlm_router.post("/rooted-frames")
async def upsert_rooted_frame(
    req: RootedFrameUpsert,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    try:
        stmt = text(
            """
            INSERT INTO nlm.rooted_frames (
                frame_id, device_id, sensor_id, protocol, sha256, merkle_root,
                storage_ref, signed, source, labels
            )
            VALUES (
                :frame_id, :device_id, :sensor_id, CAST(:protocol AS jsonb), :sha256,
                :merkle_root, :storage_ref, :signed, :source, CAST(:labels AS jsonb)
            )
            ON CONFLICT (frame_id) DO UPDATE SET
                merkle_root = COALESCE(EXCLUDED.merkle_root, nlm.rooted_frames.merkle_root),
                storage_ref = EXCLUDED.storage_ref,
                signed = EXCLUDED.signed,
                labels = EXCLUDED.labels
            RETURNING frame_id, created_at
            """
        )
        result = await db.execute(
            stmt,
            {
                "frame_id": req.frame_id,
                "device_id": req.device_id,
                "sensor_id": req.sensor_id,
                "protocol": json.dumps(req.protocol),
                "sha256": req.sha256,
                "merkle_root": req.merkle_root,
                "storage_ref": req.storage_ref,
                "signed": req.signed,
                "source": req.source,
                "labels": json.dumps(req.labels),
            },
        )
        await db.commit()
        row = result.fetchone()
        return {
            "success": True,
            "frame_id": row[0] if row else req.frame_id,
            "created_at": row[1].isoformat() if row and hasattr(row[1], "isoformat") else None,
        }
    except Exception as exc:
        await db.rollback()
        logger.exception("rooted frame upsert failed")
        raise HTTPException(status_code=500, detail=f"upsert_failed: {exc!s}") from exc


@nlm_router.get("/rooted-frames")
async def list_rooted_frames(
    limit: int = 50,
    device_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    try:
        if device_id:
            stmt = text(
                """
                SELECT frame_id, device_id, sensor_id, protocol, sha256, merkle_root,
                       storage_ref, signed, source, created_at
                FROM nlm.rooted_frames
                WHERE device_id = :device_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            )
            result = await db.execute(
                stmt, {"device_id": device_id, "limit": max(1, min(limit, 500))}
            )
        else:
            stmt = text(
                """
                SELECT frame_id, device_id, sensor_id, protocol, sha256, merkle_root,
                       storage_ref, signed, source, created_at
                FROM nlm.rooted_frames
                ORDER BY created_at DESC
                LIMIT :limit
                """
            )
            result = await db.execute(stmt, {"limit": max(1, min(limit, 500))})
        frames = []
        for r in result.fetchall():
            frames.append(
                {
                    "frame_id": r[0],
                    "device_id": r[1],
                    "sensor_id": r[2],
                    "protocol": dict(r[3]) if hasattr(r[3], "keys") else r[3],
                    "sha256": r[4],
                    "merkle_root": r[5],
                    "storage_ref": r[6],
                    "signed": bool(r[7]),
                    "source": r[8],
                    "created_at": r[9].isoformat() if r[9] and hasattr(r[9], "isoformat") else r[9],
                }
            )
        return {
            "success": True,
            "empty": len(frames) == 0,
            "count": len(frames),
            "frames": frames,
        }
    except Exception as exc:
        logger.exception("list rooted frames failed")
        return {
            "success": False,
            "empty": True,
            "count": 0,
            "frames": [],
            "error": str(exc),
            "message": "Apply migration 0040_nlm_formspace_spine_SEP23_2026.sql",
        }
