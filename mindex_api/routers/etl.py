import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import async_session_scope
from ..dependencies import get_db_session
from ..utils.deep_agent_events import schedule_domain_event

router = APIRouter(tags=["ETL & Sync"])


def _discover_etl_job_modules() -> list[str]:
    """List `mindex_etl.jobs.*` modules from the repo (filesystem scan)."""
    root = Path(__file__).resolve().parent.parent.parent / "mindex_etl" / "jobs"
    if not root.is_dir():
        return []
    out: list[str] = []
    for p in sorted(root.glob("*.py")):
        if p.name.startswith("_"):
            continue
        out.append(f"mindex_etl.jobs.{p.stem}")
    return out


@router.get("/etl/sources")
async def list_etl_sources():
    """Registered ETL job entrypoints (repo scan; empty if jobs dir missing in deployment image)."""
    return {"items": _discover_etl_job_modules(), "scanned_path": str(Path(__file__).resolve().parent.parent.parent / "mindex_etl" / "jobs")}


class SyncRequest(BaseModel):
    sources: Optional[List[str]] = ["iNaturalist", "GBIF", "MycoBank", "GenBank"]
    limit: Optional[int] = 1000

@router.post("/sync")
async def trigger_sync(request: SyncRequest):
    """Trigger an ETL sync job for the specified sources."""
    # This would normally trigger an async Celery/Kafka task
    job_id = str(uuid.uuid4())
    response = {
        "success": True,
        "message": "Sync request recorded; downstream workers must consume this job_id.",
        "job_id": job_id,
        "sources_queued": request.sources,
        "limit": request.limit,
    }
    schedule_domain_event(
        domain="search",
        task="MINDEX ETL sync triggered",
        context={
            "route": "/sync",
            "sources_queued": request.sources,
            "limit": request.limit,
            "job_id": response["job_id"],
        },
        preferred_agent="myca-research",
    )
    return response

async def _build_etl_status_payload(db: AsyncSession) -> dict[str, Any]:
    obs_result = await db.execute(text("SELECT COUNT(*)::int AS count, MAX(observed_at) AS last_seen FROM taco_observations"))
    obs_row = obs_result.mappings().first() or {"count": 0, "last_seen": None}

    env_result = await db.execute(text("SELECT COUNT(*)::int AS count, MAX(observed_at) AS last_seen FROM ocean_environments"))
    env_row = env_result.mappings().first() or {"count": 0, "last_seen": None}

    assess_result = await db.execute(text("SELECT COUNT(*)::int AS count, MAX(assessed_at) AS last_seen FROM taco_assessments"))
    assess_row = assess_result.mappings().first() or {"count": 0, "last_seen": None}

    return {
        "pipeline": "taco-maritime",
        "status": "active" if (obs_row["count"] or env_row["count"] or assess_row["count"]) else "idle",
        "sources": {
            "taco_observations": {"count": obs_row["count"], "last_seen": obs_row["last_seen"]},
            "ocean_environments": {"count": env_row["count"], "last_seen": env_row["last_seen"]},
            "taco_assessments": {"count": assess_row["count"], "last_seen": assess_row["last_seen"]},
        },
        "lastSync": max(
            [value for value in [obs_row["last_seen"], env_row["last_seen"], assess_row["last_seen"]] if value],
            default=None,
        ),
        "nextSync": None,
        "dataQuality": {
            "observation_count": obs_row["count"],
            "environment_count": env_row["count"],
            "assessment_count": assess_row["count"],
        },
        "performance": {"mode": "database-backed"},
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/etl-status")
async def get_etl_status(db: AsyncSession = Depends(get_db_session)):
    """Get the current status of the ETL pipeline (real-data only, no mocks)."""
    schedule_domain_event(
        domain="search",
        task="MINDEX ETL status requested",
        context={"route": "/etl-status"},
        preferred_agent="myca-research",
    )
    return await _build_etl_status_payload(db)


async def _pipeline_stream() -> AsyncIterator[bytes]:
    """SSE: periodic taco-maritime pipeline snapshot (same queries as /etl-status, no domain events)."""
    while True:
        try:
            async with async_session_scope() as session:
                payload = await _build_etl_status_payload(session)
            payload["stream"] = "mindex.pipeline"
            yield f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)[:200]})}\n\n".encode("utf-8")
        await asyncio.sleep(8)


@router.get("/pipeline/stream")
async def pipeline_stream():
    return StreamingResponse(_pipeline_stream(), media_type="text/event-stream")
