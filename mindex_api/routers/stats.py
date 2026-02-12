"""
Statistics Router
================
Provides database statistics and ETL sync status for dashboard widgets.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..db import get_db
from ..contracts.v1.common import TimestampedModel
from pydantic import BaseModel
from typing import Dict, Optional

router = APIRouter(prefix="/stats", tags=["statistics"])


class MINDEXStatsResponse(BaseModel):
    """Database statistics and ETL status."""
    total_taxa: int
    total_observations: int
    total_external_ids: int
    taxa_by_source: Dict[str, int]
    observations_by_source: Dict[str, int]
    observations_with_location: int
    observations_with_images: int
    taxa_with_observations: int
    observation_date_range: Dict[str, Optional[str]]
    etl_status: str  # "running" | "idle" | "unknown"
    genome_records: int = 0
    trait_records: int = 0
    synonym_records: int = 0


@router.get("", response_model=MINDEXStatsResponse)
async def get_statistics(db: AsyncSession = Depends(get_db)):
    """
    Get MINDEX database statistics and ETL sync status.
    
    Returns comprehensive statistics about the fungal database including:
    - Total counts (taxa, observations, external IDs)
    - Data breakdown by source
    - Observation quality metrics
    - ETL sync status
    """
    stats = {}
    
    # Total counts
    result = await db.execute(text("SELECT count(*) FROM core.taxon"))
    stats["total_taxa"] = result.scalar() or 0
    
    result = await db.execute(text("SELECT count(*) FROM obs.observation"))
    stats["total_observations"] = result.scalar() or 0
    
    result = await db.execute(text("SELECT count(*) FROM core.taxon_external_id"))
    stats["total_external_ids"] = result.scalar() or 0
    
    # Taxa by source
    result = await db.execute(text("""
        SELECT source, count(*) as count 
        FROM core.taxon 
        GROUP BY source 
        ORDER BY count DESC
    """))
    stats["taxa_by_source"] = {row[0]: row[1] for row in result.fetchall()}
    
    # Observations by source
    result = await db.execute(text("""
        SELECT source, count(*) as count 
        FROM obs.observation 
        GROUP BY source 
        ORDER BY count DESC
    """))
    stats["observations_by_source"] = {row[0]: row[1] for row in result.fetchall()}
    
    # Observations with location (support both PostGIS location and lat/lng columns)
    try:
        result = await db.execute(text("""
            SELECT count(*) FROM obs.observation WHERE location IS NOT NULL
        """))
        stats["observations_with_location"] = result.scalar() or 0
    except Exception:
        await db.rollback()
        result = await db.execute(text("""
            SELECT count(*) FROM obs.observation WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        """))
        stats["observations_with_location"] = result.scalar() or 0
    
    # Observations with images
    result = await db.execute(text("""
        SELECT count(*) FROM obs.observation 
        WHERE media IS NOT NULL AND media::text != '[]'
    """))
    stats["observations_with_images"] = result.scalar() or 0
    
    # Unique taxa with observations
    result = await db.execute(text("""
        SELECT count(DISTINCT taxon_id) FROM obs.observation
    """))
    stats["taxa_with_observations"] = result.scalar() or 0
    
    # Date range
    result = await db.execute(text("""
        SELECT min(observed_at), max(observed_at)
        FROM obs.observation
        WHERE observed_at IS NOT NULL
    """))
    row = result.fetchone()
    if row and row[0]:
        stats["observation_date_range"] = {
            "earliest": row[0].isoformat() if row[0] else None,
            "latest": row[1].isoformat() if row[1] else None,
        }
    else:
        stats["observation_date_range"] = {
            "earliest": None,
            "latest": None,
        }
    
    # Genome records (bio schema may not exist on all deployments)
    try:
        result = await db.execute(text("SELECT count(*) FROM bio.genome"))
        stats["genome_records"] = result.scalar() or 0
    except Exception:
        stats["genome_records"] = 0

    # Trait records (bio schema may not exist on all deployments)
    try:
        result = await db.execute(text("SELECT count(*) FROM bio.taxon_trait"))
        stats["trait_records"] = result.scalar() or 0
    except Exception:
        stats["trait_records"] = 0
    
    # Synonym records
    result = await db.execute(text("SELECT count(*) FROM core.taxon_synonym"))
    stats["synonym_records"] = result.scalar() or 0
    
    # ETL Status - Check if sync container is running
    import subprocess
    import os
    etl_status = "unknown"
    try:
        # Check for running ETL containers
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=mindex-full-sync", "--format", "{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=2,
            env=os.environ
        )
        if result.returncode == 0 and result.stdout.strip():
            status = result.stdout.strip().lower()
            if "up" in status or "running" in status:
                etl_status = "running"
            else:
                etl_status = "idle"
        else:
            etl_status = "idle"
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        # Docker not available or command failed
        etl_status = "unknown"
    
    stats["etl_status"] = etl_status
    
    return MINDEXStatsResponse(**stats)
