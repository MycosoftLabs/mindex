"""
MINDEX Agent Orchestrator — control + livestream API.
=====================================================
Read/operate the agent runtime (``mindex_etl.agents``) over HTTP:

- ``GET  /agents``                 — every sub-agent with live state
- ``GET  /agents/heartbeat``       — orchestrator liveness + stats
- ``GET  /agents/{name}``          — one agent + its recent runs
- ``POST /agents/{name}/run``      — nudge an agent to run on the next tick
- ``POST /agents/{name}/pause``    — disable an agent
- ``POST /agents/{name}/resume``   — re-enable an agent
- ``GET  /agents/runs``            — recent run history (the activity feed)
- ``GET  /agents/backups``         — AWS/NAS backup history
- ``GET  /agents/stream``          — SSE livestream of runs + heartbeat

All reads are defensive: if the ``etl.*`` schema isn't present yet (orchestrator
never started), endpoints return an empty/degraded payload instead of erroring.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import async_session_scope
from ..dependencies import get_db_session

router = APIRouter(prefix="/agents", tags=["agents", "orchestrator"])

# Heartbeats older than this mean the orchestrator process is not alive.
_LIVENESS_WINDOW_SECONDS = 120


async def _table_exists(db: AsyncSession, schema: str, table: str) -> bool:
    try:
        row = (
            await db.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = :t LIMIT 1"
                ),
                {"s": schema, "t": table},
            )
        ).first()
        return row is not None
    except Exception:
        await db.rollback()
        return False


async def _agents_rows(db: AsyncSession) -> List[Dict[str, Any]]:
    if not await _table_exists(db, "etl", "source_agent"):
        return []
    try:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT name, source, kind, concurrency_group, description, enabled,
                           priority, schedule_seconds, max_pages, domain_mode, status,
                           last_run_at, last_finished_at, next_run_at, cooldown_until,
                           last_status, last_records, last_duration_ms, last_error,
                           consecutive_failures, total_runs, total_records
                    FROM etl.source_agent
                    ORDER BY priority ASC, name ASC
                    """
                )
            )
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        await db.rollback()
        return []


def _registry_fallback() -> List[Dict[str, Any]]:
    """When the DB has no etl.* state yet, show what the runtime *would* run."""
    try:
        from mindex_etl.agents.registry import build_agents

        agents = build_agents()
        return [
            {
                "name": a.name,
                "source": a.source,
                "kind": a.kind,
                "concurrency_group": a.concurrency_group,
                "description": a.description,
                "enabled": a.enabled,
                "priority": a.priority,
                "schedule_seconds": a.schedule_seconds,
                "status": "unknown",
                "total_runs": 0,
                "total_records": 0,
            }
            for a in sorted(agents.values(), key=lambda x: x.priority)
        ]
    except Exception:
        return []


@router.get("")
@router.get("/")
async def list_agents(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    rows = await _agents_rows(db)
    source = "etl.source_agent"
    if not rows:
        rows = _registry_fallback()
        source = "registry_fallback"

    heartbeat = await _heartbeat_payload(db)
    summary = {
        "total": len(rows),
        "enabled": sum(1 for r in rows if r.get("enabled")),
        "running": sum(1 for r in rows if r.get("status") == "running"),
        "cooldown": sum(1 for r in rows if r.get("status") == "cooldown"),
        "failed": sum(1 for r in rows if r.get("status") == "failed"),
        "source_agents": sum(1 for r in rows if r.get("kind") == "source"),
        "system_agents": sum(1 for r in rows if r.get("kind") == "system"),
    }
    return {
        "orchestrator": heartbeat,
        "summary": summary,
        "agents": rows,
        "data_source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _heartbeat_payload(db: AsyncSession) -> Dict[str, Any]:
    if not await _table_exists(db, "etl", "orchestrator_heartbeat"):
        return {"alive": False, "reason": "no_heartbeat_table"}
    try:
        row = (
            await db.execute(
                text(
                    "SELECT host, pid, started_at, last_beat_at, cycle, agents_total, "
                    "agents_enabled, agents_running, max_concurrency, stats "
                    "FROM etl.orchestrator_heartbeat WHERE id = 1"
                )
            )
        ).mappings().first()
    except Exception:
        await db.rollback()
        return {"alive": False, "reason": "query_failed"}

    if not row:
        return {"alive": False, "reason": "never_started"}

    data = dict(row)
    last_beat = data.get("last_beat_at")
    alive = False
    if isinstance(last_beat, datetime):
        age = (datetime.now(timezone.utc) - last_beat).total_seconds()
        alive = age < _LIVENESS_WINDOW_SECONDS
        data["last_beat_age_seconds"] = round(age, 1)
    data["alive"] = alive
    return data


@router.get("/heartbeat")
async def get_heartbeat(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    return await _heartbeat_payload(db)


@router.get("/runs")
async def recent_runs(
    limit: int = 50,
    agent: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    if not await _table_exists(db, "etl", "agent_run"):
        return {"runs": [], "data_source": "none"}
    limit = max(1, min(500, limit))
    clause = "WHERE agent_name = :agent" if agent else ""
    params: Dict[str, Any] = {"limit": limit}
    if agent:
        params["agent"] = agent
    try:
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT id, agent_name, source, status, records, duration_ms, error,
                           host, cycle, started_at, finished_at
                    FROM etl.agent_run
                    {clause}
                    ORDER BY started_at DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
        ).mappings().all()
        return {"runs": [dict(r) for r in rows], "count": len(rows)}
    except Exception:
        await db.rollback()
        return {"runs": [], "data_source": "error"}


@router.get("/backups")
async def recent_backups(
    limit: int = 30, db: AsyncSession = Depends(get_db_session)
) -> Dict[str, Any]:
    if not await _table_exists(db, "etl", "backup_log"):
        return {"backups": [], "data_source": "none"}
    limit = max(1, min(200, limit))
    try:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT id, kind, target, status, size_bytes, object_count,
                           storage_class, error, host, started_at, finished_at
                    FROM etl.backup_log
                    ORDER BY started_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        ).mappings().all()
        return {"backups": [dict(r) for r in rows], "count": len(rows)}
    except Exception:
        await db.rollback()
        return {"backups": [], "data_source": "error"}


async def _agent_stream():
    """SSE: orchestrator heartbeat + agent summary + newest runs, every 3s."""
    last_run_id = 0
    while True:
        try:
            async with async_session_scope() as db:
                heartbeat = await _heartbeat_payload(db)
                agents = await _agents_rows(db)
                new_runs: List[Dict[str, Any]] = []
                if await _table_exists(db, "etl", "agent_run"):
                    rows = (
                        await db.execute(
                            text(
                                """
                                SELECT id, agent_name, source, status, records, duration_ms,
                                       error, started_at, finished_at
                                FROM etl.agent_run
                                WHERE id > :last
                                ORDER BY id ASC LIMIT 50
                                """
                            ),
                            {"last": last_run_id},
                        )
                    ).mappings().all()
                    new_runs = [dict(r) for r in rows]
                    if new_runs:
                        last_run_id = max(int(r["id"]) for r in new_runs)

            payload = {
                "stream": "mindex.agents",
                "ts": datetime.now(timezone.utc).isoformat(),
                "orchestrator": heartbeat,
                "summary": {
                    "total": len(agents),
                    "running": sum(1 for a in agents if a.get("status") == "running"),
                    "enabled": sum(1 for a in agents if a.get("enabled")),
                },
                "events": new_runs,
            }
            yield f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(exc)[:200]})}\n\n".encode("utf-8")
        await asyncio.sleep(3)


@router.get("/stream")
async def agent_stream() -> StreamingResponse:
    return StreamingResponse(_agent_stream(), media_type="text/event-stream")


@router.get("/{name}")
async def get_agent(name: str, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    rows = await _agents_rows(db)
    match = next((r for r in rows if r.get("name") == name), None)
    if match is None:
        # fall back to the static registry so unknown-but-valid agents still resolve
        match = next((r for r in _registry_fallback() if r.get("name") == name), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")

    runs = await recent_runs(limit=20, agent=name, db=db)
    return {"agent": match, "recent_runs": runs.get("runs", [])}


async def _update_agent(db: AsyncSession, name: str, sql: str, params: Dict[str, Any]) -> bool:
    if not await _table_exists(db, "etl", "source_agent"):
        raise HTTPException(
            status_code=503,
            detail="Agent runtime not initialized (etl.source_agent missing). "
            "Start the orchestrator: python -m mindex_etl.orchestrator",
        )
    try:
        result = await db.execute(text(sql), {**params, "name": name})
        await db.commit()
        return (result.rowcount or 0) > 0
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Update failed: {exc}")


@router.post("/{name}/run")
async def run_agent_now(name: str, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Nudge an agent to run on the orchestrator's next tick (clears cooldown)."""
    ok = await _update_agent(
        db,
        name,
        "UPDATE etl.source_agent SET next_run_at = NOW(), cooldown_until = NULL, "
        "updated_at = NOW() WHERE name = :name",
        {},
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
    return {"success": True, "agent": name, "status": "queued",
            "note": "Will run on the next orchestrator tick (within a few seconds)."}


@router.post("/{name}/pause")
async def pause_agent(name: str, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    ok = await _update_agent(
        db, name,
        "UPDATE etl.source_agent SET enabled = FALSE, status = 'disabled', "
        "updated_at = NOW() WHERE name = :name", {},
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
    return {"success": True, "agent": name, "enabled": False}


@router.post("/{name}/resume")
async def resume_agent(name: str, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    ok = await _update_agent(
        db, name,
        "UPDATE etl.source_agent SET enabled = TRUE, status = 'idle', "
        "next_run_at = NOW(), cooldown_until = NULL, updated_at = NOW() WHERE name = :name", {},
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {name}")
    return {"success": True, "agent": name, "enabled": True}
