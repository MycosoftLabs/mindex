from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

router = APIRouter(tags=["ETL & Sync"])

class SyncRequest(BaseModel):
    sources: Optional[List[str]] = ["iNaturalist", "GBIF", "MycoBank", "GenBank"]
    limit: Optional[int] = 1000

@router.post("/sync")
async def trigger_sync(request: SyncRequest):
    """Trigger an ETL sync job for the specified sources."""
    # This would normally trigger an async Celery/Kafka task
    return {
        "success": True,
        "message": "Sync started successfully",
        "job_id": "job_" + datetime.now().strftime("%Y%m%d%H%M%S"),
        "sources_queued": request.sources,
        "limit": request.limit,
    }

@router.get("/etl-status")
async def get_etl_status():
    """Get the current status of the ETL pipeline."""
    # This is a mocked logic similar to what the Next.js BFF does
    return {
        "pipeline": "active",
        "sources": {
            "inat": {"status": "connected", "taxa": 15000, "observations": 250000},
            "gbif": {"status": "connected", "taxa": 22000, "observations": 410000},
        },
        "lastSync": datetime.now().isoformat(),
        "nextSync": (datetime.now() + timedelta(hours=1)).isoformat(),
        "dataQuality": {
            "withLocation": "85.4%",
            "withImages": "92.1%",
            "verified": "76.3%",
        },
        "performance": {
            "totalTaxa": 37000,
            "totalObservations": 660000,
            "avgObservationsPerTaxon": "17.8",
        },
        "timestamp": datetime.now().isoformat(),
    }
