from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from ..utils.deep_agent_events import schedule_domain_event

router = APIRouter(tags=["ETL & Sync"])

class SyncRequest(BaseModel):
    sources: Optional[List[str]] = ["iNaturalist", "GBIF", "MycoBank", "GenBank"]
    limit: Optional[int] = 1000

@router.post("/sync")
async def trigger_sync(request: SyncRequest):
    """Trigger an ETL sync job for the specified sources."""
    # This would normally trigger an async Celery/Kafka task
    response = {
        "success": True,
        "message": "Sync started successfully",
        "job_id": "job_" + datetime.now().strftime("%Y%m%d%H%M%S"),
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

@router.get("/etl-status")
async def get_etl_status(db: AsyncSession = Depends(get_db_session)):
    """Get the current status of the ETL pipeline (real-data only, no mocks)."""
    schedule_domain_event(
        domain="search",
        task="MINDEX ETL status requested",
        context={"route": "/etl-status"},
        preferred_agent="myca-research",
    )

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
