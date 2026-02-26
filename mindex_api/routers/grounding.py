"""
Grounding router - February 17, 2026

Grounded Cognition: spatial points, episodes, experience packets,
thought objects, and reflection logs for MYCA.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, require_api_key

router = APIRouter(
    prefix="/grounding",
    tags=["grounding"],
    dependencies=[Depends(require_api_key)],
)


# --- Spatial points ---


class SpatialPointCreate(BaseModel):
    session_id: Optional[str] = None
    lat: float
    lon: float
    h3_cell: Optional[str] = None
    ep_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SpatialPointOut(BaseModel):
    id: str
    session_id: Optional[str]
    lat: float
    lon: float
    h3_cell: Optional[str]
    ep_id: Optional[str]
    metadata: Optional[Dict[str, Any]]
    created_at: datetime


@router.post("/spatial/points", response_model=SpatialPointOut)
async def create_spatial_point(
    body: SpatialPointCreate,
    db: AsyncSession = Depends(get_db_session),
) -> SpatialPointOut:
    """Store a spatial point (lat, lon) with optional H3 and EP association."""
    import json
    meta = json.dumps(body.metadata) if body.metadata else None
    stmt = text(
        """
        INSERT INTO spatial_points (session_id, lat, lon, h3_cell, ep_id, metadata)
        VALUES (:session_id, :lat, :lon, :h3_cell, :ep_id, :metadata::jsonb)
        RETURNING id::text, session_id, lat, lon, h3_cell, ep_id, metadata, created_at
        """
    )
    result = await db.execute(
        stmt,
        {
            "session_id": body.session_id,
            "lat": body.lat,
            "lon": body.lon,
            "h3_cell": body.h3_cell,
            "ep_id": body.ep_id,
            "metadata": meta or "{}",
        },
    )
    row = result.fetchone()
    await db.commit()
    return SpatialPointOut(
        id=str(row[0]),
        session_id=row[1],
        lat=float(row[2]),
        lon=float(row[3]),
        h3_cell=row[4],
        ep_id=row[5],
        metadata=row[6],
        created_at=row[7],
    )


@router.get("/spatial/points/nearby", response_model=List[Dict[str, Any]])
async def query_nearby(
    lat: float = Query(...),
    lon: float = Query(...),
    radius_km: float = Query(10.0, ge=0.1, le=500),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """Query spatial points within radius_km of (lat, lon) using PostGIS."""
    try:
        stmt = text(
            """
            SELECT id::text, session_id, lat, lon, h3_cell, ep_id, metadata, created_at,
                   ST_DistanceSphere(ST_MakePoint(lon, lat), ST_MakePoint(:lon, :lat)) / 1000.0 AS dist_km
            FROM spatial_points
            WHERE ST_DWithin(
                ST_MakePoint(lon, lat)::geography,
                ST_MakePoint(:lon, :lat)::geography,
                :radius_m
            )
            ORDER BY dist_km
            LIMIT :limit
            """
        )
        radius_m = radius_km * 1000
        result = await db.execute(
            stmt, {"lat": lat, "lon": lon, "radius_m": radius_m, "limit": limit}
        )
        rows = result.fetchall()
        return [
            {
                "id": str(r[0]),
                "session_id": r[1],
                "lat": float(r[2]),
                "lon": float(r[3]),
                "h3_cell": r[4],
                "ep_id": r[5],
                "metadata": r[6],
                "created_at": r[7].isoformat() if r[7] else None,
                "dist_km": round(float(r[8]), 4) if r[8] is not None else None,
            }
            for r in rows
        ]
    except Exception as e:
        if "postgis" in str(e).lower() or "st_makepoint" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PostGIS not available. Run migration 0016_postgis_spatial.sql",
            ) from e
        raise


@router.get("/spatial/points/h3", response_model=List[Dict[str, Any]])
async def get_by_h3_cells(
    h3_cells: str = Query(..., description="Comma-separated H3 cell IDs"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """Get spatial points in given H3 cells (for cell + neighbors from h3.grid_disk)."""
    cells = [c.strip() for c in h3_cells.split(",") if c.strip()][:20]
    if not cells:
        return []
    stmt = text(
        """
        SELECT id::text, session_id, lat, lon, h3_cell, ep_id, metadata, created_at
        FROM spatial_points
        WHERE h3_cell = ANY(:cells)
        LIMIT :limit
        """
    )
    result = await db.execute(stmt, {"cells": cells, "limit": limit})
    rows = result.fetchall()
    return [
        {
            "id": str(r[0]),
            "session_id": r[1],
            "lat": float(r[2]),
            "lon": float(r[3]),
            "h3_cell": r[4],
            "ep_id": r[5],
            "metadata": r[6],
            "created_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]


# --- Episodes ---


class EpisodeCreate(BaseModel):
    session_id: str
    start_ts: datetime
    end_ts: Optional[datetime] = None
    ep_ids: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


@router.post("/episodes")
async def create_episode(
    body: EpisodeCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Store a temporal episode."""
    import json
    ep_ids = json.dumps(body.ep_ids or [])
    meta = json.dumps(body.metadata) if body.metadata else "{}"
    stmt = text(
        """
        INSERT INTO episodes (session_id, start_ts, end_ts, ep_ids, metadata)
        VALUES (:session_id, :start_ts, :end_ts, :ep_ids::jsonb, :metadata::jsonb)
        RETURNING id::text, session_id, start_ts, end_ts, ep_ids, metadata, created_at
        """
    )
    result = await db.execute(
        stmt,
        {
            "session_id": body.session_id,
            "start_ts": body.start_ts,
            "end_ts": body.end_ts,
            "ep_ids": ep_ids,
            "metadata": meta,
        },
    )
    row = result.fetchone()
    await db.commit()
    return {
        "id": str(row[0]),
        "session_id": row[1],
        "start_ts": row[2].isoformat() if row[2] else None,
        "end_ts": row[3].isoformat() if row[3] else None,
        "ep_ids": row[4],
        "metadata": row[5],
        "created_at": row[6].isoformat() if row[6] else None,
    }


@router.get("/episodes/recent", response_model=List[Dict[str, Any]])
async def get_recent_episodes(
    session_id: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """Get recent episodes for a session."""
    stmt = text(
        """
        SELECT id::text, session_id, start_ts, end_ts, ep_ids, metadata, created_at
        FROM episodes
        WHERE session_id = :session_id
        ORDER BY start_ts DESC
        LIMIT :limit
        """
    )
    result = await db.execute(stmt, {"session_id": session_id, "limit": limit})
    rows = result.fetchall()
    return [
        {
            "id": str(r[0]),
            "session_id": r[1],
            "start_ts": r[2].isoformat() if r[2] else None,
            "end_ts": r[3].isoformat() if r[3] else None,
            "ep_ids": r[4],
            "metadata": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]


@router.patch("/episodes/close")
async def close_current_episode(
    session_id: str = Query(...),
    end_ts: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Close the current open episode for the session (set end_ts)."""
    import datetime as dt
    ts = end_ts or dt.datetime.now(dt.timezone.utc)
    stmt = text(
        """
        UPDATE episodes
        SET end_ts = :end_ts
        WHERE session_id = :session_id AND end_ts IS NULL
        RETURNING id::text, session_id, start_ts, end_ts
        """
    )
    result = await db.execute(stmt, {"session_id": session_id, "end_ts": ts})
    row = result.fetchone()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail="No open episode found for session")
    return {"id": row[0], "session_id": row[1], "start_ts": row[2].isoformat() if row[2] else None, "end_ts": ts.isoformat()}


# --- Experience packets ---


class ExperiencePacketCreate(BaseModel):
    id: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    ground_truth: Dict[str, Any]
    self_state: Optional[Dict[str, Any]] = None
    world_state: Optional[Dict[str, Any]] = None
    observation: Optional[Dict[str, Any]] = None
    uncertainty: Optional[Dict[str, Any]] = None
    provenance: Optional[Dict[str, Any]] = None


@router.post("/experience-packets")
async def create_experience_packet(
    body: ExperiencePacketCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Store an experience packet."""
    import json
    stmt = text(
        """
        INSERT INTO experience_packets (id, session_id, user_id, ground_truth, self_state, world_state, observation, uncertainty, provenance)
        VALUES (:id, :session_id, :user_id, :ground_truth::jsonb, :self_state::jsonb, :world_state::jsonb, :observation::jsonb, :uncertainty::jsonb, :provenance::jsonb)
        ON CONFLICT (id) DO UPDATE SET
            session_id = EXCLUDED.session_id,
            user_id = EXCLUDED.user_id,
            ground_truth = EXCLUDED.ground_truth,
            self_state = EXCLUDED.self_state,
            world_state = EXCLUDED.world_state,
            observation = EXCLUDED.observation,
            uncertainty = EXCLUDED.uncertainty,
            provenance = EXCLUDED.provenance
        RETURNING id, session_id, user_id, created_at
        """
    )
    result = await db.execute(
        stmt,
        {
            "id": body.id,
            "session_id": body.session_id,
            "user_id": body.user_id,
            "ground_truth": json.dumps(body.ground_truth),
            "self_state": json.dumps(body.self_state) if body.self_state else "{}",
            "world_state": json.dumps(body.world_state) if body.world_state else "{}",
            "observation": json.dumps(body.observation) if body.observation else "{}",
            "uncertainty": json.dumps(body.uncertainty) if body.uncertainty else "{}",
            "provenance": json.dumps(body.provenance) if body.provenance else "{}",
        },
    )
    row = result.fetchone()
    await db.commit()
    return {"id": row[0], "session_id": row[1], "user_id": row[2], "created_at": row[3].isoformat() if row[3] else None}


@router.get("/experience-packets/{ep_id}", response_model=Dict[str, Any])
async def get_experience_packet(
    ep_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Get an experience packet by ID."""
    stmt = text(
        """
        SELECT id, session_id, user_id, ground_truth, self_state, world_state, observation, uncertainty, provenance, created_at
        FROM experience_packets
        WHERE id = :ep_id
        """
    )
    result = await db.execute(stmt, {"ep_id": ep_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Experience packet not found")
    return {
        "id": row[0],
        "session_id": row[1],
        "user_id": row[2],
        "ground_truth": row[3],
        "self_state": row[4],
        "world_state": row[5],
        "observation": row[6],
        "uncertainty": row[7],
        "provenance": row[8],
        "created_at": row[9].isoformat() if row[9] else None,
    }


# --- Thought objects ---


class ThoughtObjectCreate(BaseModel):
    id: str
    ep_id: Optional[str] = None
    session_id: Optional[str] = None
    claim: str
    type: str
    evidence_links: Optional[List[Dict[str, Any]]] = None
    confidence: Optional[float] = None
    predicted_outcomes: Optional[Dict[str, Any]] = None
    risks: Optional[Dict[str, Any]] = None


@router.post("/thought-objects")
async def create_thought_object(
    body: ThoughtObjectCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Store a thought object."""
    import json
    ev = json.dumps(body.evidence_links or [])
    po = json.dumps(body.predicted_outcomes) if body.predicted_outcomes else "{}"
    risks = json.dumps(body.risks) if body.risks else "{}"
    stmt = text(
        """
        INSERT INTO thought_objects (id, ep_id, session_id, claim, type, evidence_links, confidence, predicted_outcomes, risks)
        VALUES (:id, :ep_id, :session_id, :claim, :type, :evidence_links::jsonb, :confidence, :predicted_outcomes::jsonb, :risks::jsonb)
        ON CONFLICT (id) DO UPDATE SET
            ep_id = EXCLUDED.ep_id,
            session_id = EXCLUDED.session_id,
            claim = EXCLUDED.claim,
            type = EXCLUDED.type,
            evidence_links = EXCLUDED.evidence_links,
            confidence = EXCLUDED.confidence,
            predicted_outcomes = EXCLUDED.predicted_outcomes,
            risks = EXCLUDED.risks
        RETURNING id, ep_id, session_id, created_at
        """
    )
    result = await db.execute(
        stmt,
        {
            "id": body.id,
            "ep_id": body.ep_id,
            "session_id": body.session_id,
            "claim": body.claim,
            "type": body.type,
            "evidence_links": ev,
            "confidence": body.confidence,
            "predicted_outcomes": po,
            "risks": risks,
        },
    )
    row = result.fetchone()
    await db.commit()
    return {"id": row[0], "ep_id": row[1], "session_id": row[2], "created_at": row[3].isoformat() if row[3] else None}


@router.get("/thought-objects", response_model=List[Dict[str, Any]])
async def list_thought_objects(
    session_id: Optional[str] = Query(None),
    ep_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """List thought objects, optionally filtered by session_id or ep_id."""
    where = []
    params = {"limit": limit}
    if session_id:
        where.append("session_id = :session_id")
        params["session_id"] = session_id
    if ep_id:
        where.append("ep_id = :ep_id")
        params["ep_id"] = ep_id
    where_sql = " AND ".join(where) if where else "TRUE"
    stmt = text(
        f"""
        SELECT id, ep_id, session_id, claim, type, evidence_links, confidence, predicted_outcomes, risks, created_at
        FROM thought_objects
        WHERE {where_sql}
        ORDER BY created_at DESC
        LIMIT :limit
        """
    )
    result = await db.execute(stmt, params)
    rows = result.fetchall()
    return [
        {
            "id": r[0],
            "ep_id": r[1],
            "session_id": r[2],
            "claim": r[3],
            "type": r[4],
            "evidence_links": r[5],
            "confidence": r[6],
            "predicted_outcomes": r[7],
            "risks": r[8],
            "created_at": r[9].isoformat() if r[9] else None,
        }
        for r in rows
    ]


# --- Reflection logs ---


class ReflectionLogCreate(BaseModel):
    ep_id: Optional[str] = None
    session_id: Optional[str] = None
    response: Optional[str] = None
    prediction: Optional[str] = None
    actual: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@router.post("/reflection-logs")
async def create_reflection_log(
    body: ReflectionLogCreate,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Store a reflection log entry."""
    import json
    meta = json.dumps(body.metadata) if body.metadata else "{}"
    stmt = text(
        """
        INSERT INTO reflection_logs (ep_id, session_id, response, prediction, actual, metadata)
        VALUES (:ep_id, :session_id, :response, :prediction, :actual, :metadata::jsonb)
        RETURNING id::text, ep_id, session_id, created_at
        """
    )
    result = await db.execute(
        stmt,
        {
            "ep_id": body.ep_id,
            "session_id": body.session_id,
            "response": body.response,
            "prediction": body.prediction,
            "actual": body.actual,
            "metadata": meta,
        },
    )
    row = result.fetchone()
    await db.commit()
    return {"id": row[0], "ep_id": row[1], "session_id": row[2], "created_at": row[3].isoformat() if row[3] else None}


@router.get("/reflection-logs/history", response_model=List[Dict[str, Any]])
async def get_reflection_history(
    session_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """Get recent reflection log entries."""
    stmt = text(
        """
        SELECT id::text, ep_id, session_id, response, prediction, actual, metadata, created_at
        FROM reflection_logs
        WHERE (:session_id::text IS NULL OR session_id = :session_id)
        ORDER BY created_at DESC
        LIMIT :limit
        """
    )
    result = await db.execute(stmt, {"session_id": session_id or None, "limit": limit})
    rows = result.fetchall()
    return [
        {
            "id": r[0],
            "ep_id": r[1],
            "session_id": r[2],
            "response": r[3],
            "prediction": r[4],
            "actual": r[5],
            "metadata": r[6],
            "created_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]
