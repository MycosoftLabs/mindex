"""TAC-O API Router — Tactical Oceanography Endpoints.

Observation ingestion, assessment retrieval, and sensor status
for contractor-integrated NUWC TAC-O operations.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/taco", tags=["taco"])


class TACOObservationCreate(BaseModel):
    sensor_id: str
    sensor_type: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    depth_m: Optional[float] = None
    raw_data: Optional[Dict[str, Any]] = None
    processed_fingerprint: Optional[Dict[str, Any]] = None
    classification: Optional[Dict[str, Any]] = None
    anomaly_score: Optional[float] = None
    confidence: Optional[float] = None
    avani_review: Optional[str] = None
    observed_at: str
    merkle_hash: Optional[str] = None


class TACOAssessmentCreate(BaseModel):
    observation_ids: List[str]
    assessment_type: str
    classification: Optional[Dict[str, Any]] = None
    recommendation: Optional[Dict[str, Any]] = None
    sonar_performance: Optional[Dict[str, Any]] = None
    urgency: Optional[float] = None
    avani_ecological_check: Optional[Dict[str, Any]] = None
    merkle_hash: Optional[str] = None


@router.post("/observations")
async def ingest_observation(obs: TACOObservationCreate, db: AsyncSession = Depends(get_db_session)):
    """Ingest a sensor observation from the maritime sensor network."""
    result = await db.execute(
        text(
            """
            INSERT INTO taco_observations (
                sensor_id, sensor_type, location, depth_m, raw_data, processed_fingerprint,
                nlm_classification, anomaly_score, confidence, avani_review, observed_at, merkle_hash
            ) VALUES (
                :sensor_id,
                :sensor_type,
                CASE
                    WHEN :longitude IS NOT NULL AND :latitude IS NOT NULL
                    THEN ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
                    ELSE NULL
                END,
                :depth_m,
                CAST(:raw_data AS jsonb),
                CAST(:processed_fingerprint AS jsonb),
                CAST(:classification AS jsonb),
                :anomaly_score,
                :confidence,
                :avani_review,
                :observed_at,
                :merkle_hash
            )
            RETURNING observation_id
            """
        ),
        {
            **obs.model_dump(),
            "raw_data": obs.raw_data and __import__("json").dumps(obs.raw_data),
            "processed_fingerprint": obs.processed_fingerprint and __import__("json").dumps(obs.processed_fingerprint),
            "classification": obs.classification and __import__("json").dumps(obs.classification),
        },
    )
    await db.commit()
    return {"status": "created", "observation_id": str(result.scalar_one())}


@router.get("/observations")
async def list_observations(
    sensor_id: Optional[str] = None,
    sensor_type: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
    """List TAC-O sensor observations with optional filters."""
    where_clauses = []
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if sensor_id:
        where_clauses.append("sensor_id = :sensor_id")
        params["sensor_id"] = sensor_id
    if sensor_type:
        where_clauses.append("sensor_type = :sensor_type")
        params["sensor_type"] = sensor_type
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    result = await db.execute(
        text(
            f"""
            SELECT observation_id, sensor_id, sensor_type,
                   ST_Y(location::geometry) AS latitude, ST_X(location::geometry) AS longitude,
                   depth_m, raw_data, processed_fingerprint, nlm_classification,
                   anomaly_score, confidence, avani_review, observed_at, ingested_at, merkle_hash
            FROM taco_observations
            {where_sql}
            ORDER BY observed_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    rows = result.mappings().all()
    return {"observations": [dict(row) for row in rows], "total": len(rows), "limit": limit, "offset": offset}


@router.get("/observations/{observation_id}")
async def get_observation(observation_id: UUID, db: AsyncSession = Depends(get_db_session)):
    """Get a specific TAC-O observation by ID."""
    result = await db.execute(
        text(
            """
            SELECT observation_id, sensor_id, sensor_type,
                   ST_Y(location::geometry) AS latitude, ST_X(location::geometry) AS longitude,
                   depth_m, raw_data, processed_fingerprint, nlm_classification,
                   anomaly_score, confidence, avani_review, observed_at, ingested_at, merkle_hash
            FROM taco_observations
            WHERE observation_id = :observation_id
            LIMIT 1
            """
        ),
        {"observation_id": observation_id},
    )
    row = result.mappings().first()
    return {"observation": dict(row)} if row else {"observation_id": str(observation_id), "status": "not_found"}


@router.post("/assessments")
async def create_assessment(assessment: TACOAssessmentCreate, db: AsyncSession = Depends(get_db_session)):
    """Store an AI-generated tactical assessment."""
    result = await db.execute(
        text(
            """
            INSERT INTO taco_assessments (
                observation_ids, assessment_type, classification, recommendation,
                sonar_performance, urgency, avani_ecological_check, merkle_hash
            ) VALUES (
                :observation_ids,
                :assessment_type,
                CAST(:classification AS jsonb),
                CAST(:recommendation AS jsonb),
                CAST(:sonar_performance AS jsonb),
                :urgency,
                CAST(:avani_ecological_check AS jsonb),
                :merkle_hash
            )
            RETURNING assessment_id
            """
        ),
        {
            **assessment.model_dump(),
            "classification": assessment.classification and __import__("json").dumps(assessment.classification),
            "recommendation": assessment.recommendation and __import__("json").dumps(assessment.recommendation),
            "sonar_performance": assessment.sonar_performance and __import__("json").dumps(assessment.sonar_performance),
            "avani_ecological_check": assessment.avani_ecological_check and __import__("json").dumps(assessment.avani_ecological_check),
        },
    )
    await db.commit()
    return {"status": "created", "assessment_id": str(result.scalar_one())}


@router.get("/assessments")
async def list_assessments(
    assessment_type: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
    """List TAC-O tactical assessments."""
    where_sql = "WHERE assessment_type = :assessment_type" if assessment_type else ""
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if assessment_type:
        params["assessment_type"] = assessment_type
    result = await db.execute(
        text(
            f"""
            SELECT assessment_id, observation_ids, assessment_type, classification,
                   recommendation, sonar_performance, urgency, avani_ecological_check,
                   operator_action_taken, assessed_at, merkle_hash
            FROM taco_assessments
            {where_sql}
            ORDER BY assessed_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    rows = result.mappings().all()
    return {"assessments": [dict(row) for row in rows], "total": len(rows), "limit": limit, "offset": offset}


@router.get("/sensor-status")
async def sensor_status(db: AsyncSession = Depends(get_db_session)):
    """Get status of all maritime sensors in the network."""
    result = await db.execute(
        text(
            """
            SELECT DISTINCT ON (sensor_id)
                   sensor_id,
                   sensor_type,
                   observed_at AS last_seen,
                   confidence,
                   anomaly_score,
                   ST_Y(location::geometry) AS latitude,
                   ST_X(location::geometry) AS longitude
            FROM taco_observations
            ORDER BY sensor_id, observed_at DESC
            """
        )
    )
    rows = result.mappings().all()
    sensors = []
    for row in rows:
        sensors.append(
            {
                "sensor_id": row["sensor_id"],
                "sensor_type": row["sensor_type"],
                "last_seen": row["last_seen"],
                "confidence": row["confidence"],
                "anomaly_score": row["anomaly_score"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "status": "online",
            }
        )
    return {"sensors": sensors, "total": len(sensors), "network_status": "online" if sensors else "degraded"}


@router.get("/health")
async def taco_health():
    """TAC-O subsystem health check."""
    return {
        "status": "healthy",
        "subsystem": "taco",
        "tables": ["taco_observations", "taco_assessments", "acoustic_signatures",
                    "ocean_environments", "magnetic_baselines"],
    }
