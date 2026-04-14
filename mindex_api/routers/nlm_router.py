"""NLM and TAC-O inference router for MINDEX."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

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
