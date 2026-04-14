from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

router = APIRouter(prefix="/fusarium", tags=["fusarium-analytics"])


class EntityTrackUpsert(BaseModel):
    track_id: str | None = None
    latest_label: str
    confidence: float
    latitude: float | None = None
    longitude: float | None = None


class CorrelationEventCreate(BaseModel):
    event_id: str | None = None
    entity_id: str | None = None
    domains: list[str]
    confidence: float
    payload: dict = {}


@router.post("/entity-tracks")
async def upsert_entity_track(track: EntityTrackUpsert, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            INSERT INTO fusarium.entity_tracks (
                track_id, latest_label, confidence, first_seen, last_seen, last_position
            ) VALUES (
                COALESCE(CAST(:track_id AS uuid), gen_random_uuid()),
                :latest_label,
                :confidence,
                NOW(),
                NOW(),
                CASE
                    WHEN :longitude IS NOT NULL AND :latitude IS NOT NULL
                    THEN ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
                    ELSE NULL
                END
            )
            ON CONFLICT (track_id) DO UPDATE SET
                latest_label = EXCLUDED.latest_label,
                confidence = EXCLUDED.confidence,
                last_seen = NOW(),
                last_position = EXCLUDED.last_position
            RETURNING track_id
            """
        ),
        track.model_dump(),
    )
    await db.commit()
    return {"status": "upserted", "track_id": str(result.scalar_one())}


@router.post("/correlation-events")
async def create_correlation_event(event: CorrelationEventCreate, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            INSERT INTO fusarium.correlation_events (
                event_id, entity_id, domains, confidence, payload, created_at
            ) VALUES (
                COALESCE(CAST(:event_id AS uuid), gen_random_uuid()),
                CAST(:entity_id AS uuid),
                :domains::text[],
                :confidence,
                CAST(:payload AS jsonb),
                NOW()
            )
            RETURNING event_id
            """
        ),
        {
            **event.model_dump(),
            "payload": __import__("json").dumps(event.payload),
        },
    )
    await db.commit()
    return {"status": "created", "event_id": str(result.scalar_one())}


@router.get("/entity-tracks")
async def entity_tracks(limit: int = Query(default=200, ge=1, le=1000), db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            SELECT track_id, latest_label, confidence, ST_Y(last_position::geometry) AS lat,
                   ST_X(last_position::geometry) AS lon, first_seen, last_seen
            FROM fusarium.entity_tracks
            ORDER BY last_seen DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return {"tracks": [dict(row) for row in rows], "total": len(rows)}


@router.get("/correlation-events")
async def correlation_events(limit: int = Query(default=200, ge=1, le=1000), db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            SELECT event_id, entity_id, domains, confidence, created_at
            FROM fusarium.correlation_events
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return {"events": [dict(row) for row in rows], "total": len(rows)}


@router.get("/mission-summary")
async def mission_summary(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        text(
            """
            SELECT
              COUNT(*)::int AS total_events,
              COUNT(DISTINCT entity_id)::int AS total_entities,
              COALESCE(AVG(confidence), 0)::float AS avg_confidence
            FROM fusarium.correlation_events
            """
        )
    )
    row = result.mappings().first() or {"total_events": 0, "total_entities": 0, "avg_confidence": 0.0}
    return dict(row)
