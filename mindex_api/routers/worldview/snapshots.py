"""Internal MINDEX store for AVANI-governed MAS WorldState snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import async_session_scope, get_db
from ...worldview_snapshot_meta import snapshot_to_avani_meta

router = APIRouter(prefix="/worldview/snapshots", tags=["worldview-internal-snapshots"])


class WorldviewSnapshotIn(BaseModel):
    snapshot_id: str
    captured_at: datetime
    region: Dict[str, Any] = Field(default_factory=dict)
    world_payload: Dict[str, Any] = Field(default_factory=dict)
    summary_payload: Dict[str, Any] = Field(default_factory=dict)
    sources_payload: Dict[str, Any] = Field(default_factory=dict)
    source_counts: Dict[str, Any] = Field(default_factory=dict)
    source_freshness: Dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False
    confidence: float = 1.0
    provenance: Dict[str, Any] = Field(default_factory=dict)
    avani_verdict: str = "allow"
    audit_trail_id: Optional[str] = None
    entry_hash: Optional[str] = None


CREATE_TABLE_SQL = """
CREATE SCHEMA IF NOT EXISTS worldview;
CREATE TABLE IF NOT EXISTS worldview.worldview_state_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    captured_at TIMESTAMPTZ NOT NULL,
    region JSONB NOT NULL DEFAULT '{}'::jsonb,
    world_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    sources_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_freshness JSONB NOT NULL DEFAULT '{}'::jsonb,
    degraded BOOLEAN NOT NULL DEFAULT FALSE,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    avani_verdict TEXT NOT NULL DEFAULT 'allow',
    audit_trail_id TEXT,
    entry_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_worldview_state_snapshots_captured_at
    ON worldview.worldview_state_snapshots (captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_worldview_state_snapshots_region
    ON worldview.worldview_state_snapshots USING GIN (region);
CREATE INDEX IF NOT EXISTS idx_worldview_state_snapshots_avani_verdict
    ON worldview.worldview_state_snapshots (avani_verdict);
CREATE INDEX IF NOT EXISTS idx_worldview_state_snapshots_audit_trail_id
    ON worldview.worldview_state_snapshots (audit_trail_id);
"""


async def ensure_snapshot_table(db: AsyncSession) -> None:
    for statement in [part.strip() for part in CREATE_TABLE_SQL.split(";") if part.strip()]:
        await db.execute(text(statement))
    await db.commit()


def _row_to_dict(row: Any | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row._mapping)
    for key in (
        "captured_at",
        "created_at",
    ):
        value = data.get(key)
        if isinstance(value, datetime):
            data[key] = value.astimezone(timezone.utc).isoformat()
    return data


async def insert_snapshot(db: AsyncSession, snapshot: WorldviewSnapshotIn) -> Dict[str, Any]:
    await ensure_snapshot_table(db)
    await db.execute(
        text(
            """
            INSERT INTO worldview.worldview_state_snapshots (
                snapshot_id, captured_at, region, world_payload, summary_payload,
                sources_payload, source_counts, source_freshness, degraded, confidence,
                provenance, avani_verdict, audit_trail_id, entry_hash
            )
            VALUES (
                :snapshot_id, :captured_at, CAST(:region AS JSONB), CAST(:world_payload AS JSONB),
                CAST(:summary_payload AS JSONB), CAST(:sources_payload AS JSONB),
                CAST(:source_counts AS JSONB), CAST(:source_freshness AS JSONB),
                :degraded, :confidence, CAST(:provenance AS JSONB), :avani_verdict,
                :audit_trail_id, :entry_hash
            )
            ON CONFLICT (snapshot_id) DO UPDATE SET
                captured_at = EXCLUDED.captured_at,
                region = EXCLUDED.region,
                world_payload = EXCLUDED.world_payload,
                summary_payload = EXCLUDED.summary_payload,
                sources_payload = EXCLUDED.sources_payload,
                source_counts = EXCLUDED.source_counts,
                source_freshness = EXCLUDED.source_freshness,
                degraded = EXCLUDED.degraded,
                confidence = EXCLUDED.confidence,
                provenance = EXCLUDED.provenance,
                avani_verdict = EXCLUDED.avani_verdict,
                audit_trail_id = EXCLUDED.audit_trail_id,
                entry_hash = EXCLUDED.entry_hash
            RETURNING *
            """
        ),
        {
            **snapshot.model_dump(),
            "captured_at": snapshot.captured_at,
            "region": json.dumps(snapshot.region, default=str),
            "world_payload": json.dumps(snapshot.world_payload, default=str),
            "summary_payload": json.dumps(snapshot.summary_payload, default=str),
            "sources_payload": json.dumps(snapshot.sources_payload, default=str),
            "source_counts": json.dumps(snapshot.source_counts, default=str),
            "source_freshness": json.dumps(snapshot.source_freshness, default=str),
            "provenance": json.dumps(snapshot.provenance, default=str),
        },
    )
    await db.commit()
    row = await get_snapshot(db, snapshot.snapshot_id)
    assert row is not None
    return row


async def get_snapshot(db: AsyncSession, snapshot_id: str) -> Optional[Dict[str, Any]]:
    await ensure_snapshot_table(db)
    result = await db.execute(
        text("SELECT * FROM worldview.worldview_state_snapshots WHERE snapshot_id = :snapshot_id"),
        {"snapshot_id": snapshot_id},
    )
    return _row_to_dict(result.first())


async def get_latest_snapshot(db: AsyncSession, region: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    await ensure_snapshot_table(db)
    # Region-aware selection can become stricter once MAS starts materializing many regions.
    result = await db.execute(
        text(
            """
            SELECT * FROM worldview.worldview_state_snapshots
            ORDER BY captured_at DESC
            LIMIT 1
            """
        )
    )
    return _row_to_dict(result.first())


async def get_latest_snapshot_meta(region: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        async with async_session_scope() as db:
            return snapshot_to_avani_meta(await get_latest_snapshot(db, region=region))
    except Exception as exc:
        return {
            "worldstate_snapshot_id": None,
            "freshness": "degraded",
            "degraded": True,
            "confidence": 0.35,
            "provenance": {"source": "mindex_worldview_snapshot_store", "error": str(exc)},
            "audit_trail_id": None,
        }


@router.post("")
async def create_worldview_snapshot(
    snapshot: WorldviewSnapshotIn,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    stored = await insert_snapshot(db, snapshot)
    return {"status": "stored", "snapshot": stored}


@router.get("/latest")
async def latest_worldview_snapshot(
    region: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    snapshot = await get_latest_snapshot(db, region={"raw": region} if region else None)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No Worldview snapshot available")
    return {"snapshot": snapshot}


@router.get("/{snapshot_id}")
async def worldview_snapshot_by_id(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    snapshot = await get_snapshot(db, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Worldview snapshot not found")
    return {"snapshot": snapshot}
