"""Network / storage federation API (MINDEX App Overhaul — May 03, 2026)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from ..db import async_session_scope

router = APIRouter(tags=["Network"])


@router.get("/network/nodes")
async def list_storage_nodes(db: AsyncSession = Depends(get_db_session)):
    try:
        result = await db.execute(
            text(
                """
                SELECT id, kind, label, host, region, capacity_bytes, used_bytes, owner,
                       last_seen_at, created_at
                FROM network.storage_node
                ORDER BY created_at DESC
                LIMIT 200
                """
            )
        )
        return {"items": [dict(r) for r in result.mappings().all()]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"storage_node_unavailable:{str(exc)[:120]}") from exc


@router.get("/network/nodes/{node_id}")
async def get_storage_node(node_id: str, db: AsyncSession = Depends(get_db_session)):
    row = (
        await db.execute(
            text("SELECT * FROM network.storage_node WHERE id = :id::uuid"),
            {"id": node_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="node_not_found")
    return dict(row)


@router.get("/network/edges")
async def list_edges(db: AsyncSession = Depends(get_db_session)):
    """Shard placements (federation); treat as graph edges to storage nodes."""
    try:
        result = await db.execute(
            text(
                """
                SELECT s.id, s.storage_node_id, s.shard_key, s.created_at,
                       n.label AS storage_node_label
                FROM network.shard s
                JOIN network.storage_node n ON n.id = s.storage_node_id
                ORDER BY s.created_at DESC
                LIMIT 500
                """
            )
        )
        return {"items": [dict(r) for r in result.mappings().all()]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"shard_unavailable:{str(exc)[:120]}") from exc


async def _sensor_sse() -> AsyncIterator[bytes]:
    while True:
        payload: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "devices": []}
        try:
            async with async_session_scope() as session:
                rows = (
                    await session.execute(
                        text(
                            """
                            SELECT st.device_id, smp.recorded_at, smp.value_json AS payload
                            FROM telemetry.sample smp
                            JOIN telemetry.stream st ON st.id = smp.stream_id
                            ORDER BY smp.recorded_at DESC
                            LIMIT 5
                            """
                        )
                    )
                ).mappings().all()
                payload["devices"] = [dict(r) for r in rows]
        except Exception as exc:
            payload["error"] = str(exc)[:200]
        yield f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
        await asyncio.sleep(3)


@router.get("/network/devices/live-sensors")
async def live_sensors_stream():
    return StreamingResponse(_sensor_sse(), media_type="text/event-stream")


@router.post("/network/nodes/{node_id}/refresh")
async def refresh_node(node_id: str):
    return {"status": "accepted", "node_id": node_id, "note": "Collector refresh hooks run out-of-band on VM"}
