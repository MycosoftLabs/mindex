"""
MINDEX Images API Router
=========================
Endpoints for managing fungal species images:
- Get image statistics
- Trigger image backfill jobs
- Check backfill job status
- Search for images by species
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..dependencies import require_api_key

router = APIRouter(prefix="/images", tags=["Images"])


# =============================================================================
# Schemas
# =============================================================================

class ImageStats(BaseModel):
    """Statistics about images in the database."""
    total_taxa: int = Field(..., description="Total number of taxa in database")
    taxa_with_images: int = Field(..., description="Taxa that have images")
    taxa_without_images: int = Field(..., description="Taxa missing images")
    coverage_percent: float = Field(..., description="Percentage of taxa with images")
    by_source: Dict[str, int] = Field(default_factory=dict, description="Image count by source")


class BackfillJobRequest(BaseModel):
    """Request to start a backfill job."""
    limit: int = Field(default=100, ge=1, le=10000, description="Max taxa to process")
    sources: Optional[List[str]] = Field(
        default=None,
        description="Image sources to use (inat, wikipedia, gbif, mushroom_observer, flickr, bing)"
    )
    source_filter: Optional[str] = Field(
        default=None,
        description="Only process taxa from this source"
    )
    priority: str = Field(
        default="popular",
        description="Priority order: 'popular' (by observations) or 'alphabetical'"
    )


class BackfillJobStatus(BaseModel):
    """Status of a backfill job."""
    status: str = Field(..., description="Job status: pending, running, completed, failed")
    processed: int = Field(default=0, description="Taxa processed so far")
    found: int = Field(default=0, description="Images found")
    not_found: int = Field(default=0, description="Taxa where no image was found")
    errors: int = Field(default=0, description="Errors encountered")
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    message: Optional[str] = None


class TaxonImageInfo(BaseModel):
    """Image information for a taxon."""
    taxon_id: str
    canonical_name: str
    has_image: bool
    image_url: Optional[str] = None
    image_source: Optional[str] = None
    observations_count: int = 0


class ImageSearchResult(BaseModel):
    """Result of an image search."""
    url: str
    source: str
    quality_score: float
    medium_url: Optional[str] = None
    original_url: Optional[str] = None
    photographer: Optional[str] = None


# =============================================================================
# In-memory job tracking (for demo - use Redis/DB in production)
# =============================================================================

_active_jobs: Dict[str, Dict[str, Any]] = {}
CHECKPOINT_FILE = Path("C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_scrape/backfill_images_checkpoint.json")


def _get_checkpoint() -> Dict[str, Any]:
    """Load checkpoint file if exists."""
    if CHECKPOINT_FILE.exists():
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/stats", response_model=ImageStats)
async def get_image_stats(
    db: AsyncSession = Depends(get_db),
    _api_key: Optional[str] = Depends(require_api_key),
) -> ImageStats:
    """
    Get statistics about image coverage in the database.
    
    Returns counts of taxa with/without images and breakdown by source.
    """
    # Get total taxa count
    total_result = await db.execute(text("SELECT COUNT(*) as count FROM core.taxon"))
    total_row = total_result.mappings().one()
    total_taxa = total_row["count"]
    
    # Get taxa with images
    with_images_result = await db.execute(text("""
        SELECT COUNT(*) as count 
        FROM core.taxon 
        WHERE (metadata->'default_photo'->>'url') IS NOT NULL
          AND (metadata->'default_photo'->>'url') != ''
    """))
    with_images_row = with_images_result.mappings().one()
    taxa_with_images = with_images_row["count"]
    
    # Get breakdown by image source
    by_source_result = await db.execute(text("""
        SELECT 
            COALESCE(metadata->'default_photo'->>'source', 'unknown') as source,
            COUNT(*) as count
        FROM core.taxon 
        WHERE (metadata->'default_photo'->>'url') IS NOT NULL
          AND (metadata->'default_photo'->>'url') != ''
        GROUP BY metadata->'default_photo'->>'source'
        ORDER BY count DESC
    """))
    by_source = {row["source"]: row["count"] for row in by_source_result.mappings()}
    
    taxa_without = total_taxa - taxa_with_images
    coverage = (taxa_with_images / total_taxa * 100) if total_taxa > 0 else 0
    
    return ImageStats(
        total_taxa=total_taxa,
        taxa_with_images=taxa_with_images,
        taxa_without_images=taxa_without,
        coverage_percent=round(coverage, 2),
        by_source=by_source,
    )


@router.get("/missing", response_model=List[TaxonImageInfo])
async def get_taxa_missing_images(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    source: Optional[str] = Query(default=None, description="Filter by taxon source"),
    db: AsyncSession = Depends(get_db),
    _api_key: Optional[str] = Depends(require_api_key),
) -> List[TaxonImageInfo]:
    """
    Get a list of taxa that are missing images.
    
    Results are ordered by observations_count (most popular first).
    """
    query = """
        SELECT 
            id,
            canonical_name,
            source,
            (metadata->>'observations_count')::int as observations_count
        FROM core.taxon
        WHERE (metadata->'default_photo'->>'url') IS NULL
           OR (metadata->'default_photo'->>'url') = ''
    """
    params = {"limit": limit, "offset": offset}
    
    if source:
        query += " AND source = :source"
        params["source"] = source
    
    query += """
        ORDER BY 
            CASE 
                WHEN (metadata->>'observations_count') ~ '^[0-9]+$' 
                THEN (metadata->>'observations_count')::bigint 
                ELSE 0 
            END DESC
        LIMIT :limit OFFSET :offset
    """
    
    result = await db.execute(text(query), params)
    rows = result.mappings().all()
    
    return [
        TaxonImageInfo(
            taxon_id=str(row["id"]),
            canonical_name=row["canonical_name"],
            has_image=False,
            observations_count=row["observations_count"] or 0,
        )
        for row in rows
    ]


@router.post("/backfill/start", response_model=BackfillJobStatus)
async def start_backfill_job(
    request: BackfillJobRequest,
    background_tasks: BackgroundTasks,
    _api_key: Optional[str] = Depends(require_api_key),
) -> BackfillJobStatus:
    """
    Start a background job to backfill missing images.
    
    The job will search multiple sources (iNaturalist, Wikipedia, GBIF, etc.)
    for images of species that don't have them.
    """
    import uuid
    import asyncio
    
    job_id = str(uuid.uuid4())
    
    # Initialize job status
    _active_jobs[job_id] = {
        "status": "pending",
        "processed": 0,
        "found": 0,
        "not_found": 0,
        "errors": 0,
        "started_at": datetime.now().isoformat(),
        "completed_at": None,
        "request": request.model_dump(),
    }
    
    # Start background task - inline implementation using API's async DB
    async def run_backfill():
        try:
            _active_jobs[job_id]["status"] = "running"
            
            # Import the multi-source fetcher
            import sys
            import os
            # Add mindex_etl to path if needed
            etl_path = os.path.join(os.path.dirname(__file__), "..", "..", "mindex_etl")
            if etl_path not in sys.path:
                sys.path.insert(0, os.path.dirname(etl_path))
            
            from mindex_etl.sources.multi_image import MultiSourceImageFetcher
            
            # Get taxa missing images using async DB
            from ..db import get_db, _ensure_engine, _session_factory
            _ensure_engine()
            
            async with _session_factory() as db:
                # Get missing taxa
                result = await db.execute(text("""
                    SELECT id, canonical_name, source
                    FROM core.taxon
                    WHERE (metadata->'default_photo'->>'url') IS NULL
                       OR (metadata->'default_photo'->>'url') = ''
                    ORDER BY 
                        CASE 
                            WHEN (metadata->>'observations_count') ~ '^[0-9]+$' 
                            THEN (metadata->>'observations_count')::bigint 
                            ELSE 0 
                        END DESC
                    LIMIT :limit
                """), {"limit": request.limit})
                taxa = result.mappings().all()
                
                if not taxa:
                    _active_jobs[job_id].update({
                        "status": "completed",
                        "message": "No taxa missing images",
                        "completed_at": datetime.now().isoformat(),
                    })
                    return
                
                # Process each taxon
                async with MultiSourceImageFetcher() as fetcher:
                    for taxon in taxa:
                        taxon_id = str(taxon["id"])
                        canonical_name = taxon["canonical_name"]
                        
                        try:
                            # Search for image
                            image = await fetcher.find_best_image(
                                canonical_name, 
                                request.sources
                            )
                            
                            if image:
                                # Build photo data
                                photo_data = {
                                    "url": image.url,
                                    "medium_url": image.medium_url or image.url,
                                    "original_url": image.original_url or image.url,
                                    "source": image.source,
                                    "attribution": image.photographer,
                                    "license_code": image.license,
                                    "scraped_at": datetime.now().isoformat(),
                                    "quality_score": image.quality_score,
                                }
                                
                                # Update taxon - use cast for asyncpg compatibility
                                photo_json = json.dumps(photo_data)
                                await db.execute(
                                    text("""
                                        UPDATE core.taxon
                                        SET 
                                            metadata = jsonb_set(
                                                COALESCE(metadata, '{}'::jsonb),
                                                '{default_photo}',
                                                cast(:photo_data as jsonb),
                                                true
                                            ),
                                            updated_at = NOW()
                                        WHERE id = cast(:id as uuid)
                                    """),
                                    {"id": taxon_id, "photo_data": photo_json},
                                )
                                _active_jobs[job_id]["found"] += 1
                            else:
                                _active_jobs[job_id]["not_found"] += 1
                            
                            _active_jobs[job_id]["processed"] += 1
                            
                            # Commit every 10 updates
                            if _active_jobs[job_id]["processed"] % 10 == 0:
                                await db.commit()
                            
                            # Rate limit
                            await asyncio.sleep(0.5)
                            
                        except Exception as e:
                            _active_jobs[job_id]["errors"] += 1
                            print(f"Error processing {canonical_name}: {e}")
                            continue
                    
                    # Final commit
                    await db.commit()
            
            _active_jobs[job_id].update({
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
            })
            
        except Exception as e:
            import traceback
            _active_jobs[job_id].update({
                "status": "failed",
                "completed_at": datetime.now().isoformat(),
                "message": f"{str(e)}\n{traceback.format_exc()}",
            })
    
    background_tasks.add_task(run_backfill)
    
    return BackfillJobStatus(
        status="pending",
        message=f"Backfill job started with ID: {job_id}. Check /images/backfill/status for progress.",
    )


@router.get("/backfill/status", response_model=BackfillJobStatus)
async def get_backfill_status(
    _api_key: Optional[str] = Depends(require_api_key),
) -> BackfillJobStatus:
    """
    Get the status of the current or last backfill job.
    
    Returns checkpoint data if a job has been run.
    """
    # Check for active jobs
    for job_id, job in _active_jobs.items():
        if job["status"] in ("pending", "running"):
            return BackfillJobStatus(**{k: v for k, v in job.items() if k != "request"})
    
    # Check checkpoint file
    checkpoint = _get_checkpoint()
    if checkpoint:
        stats = checkpoint.get("stats", {})
        return BackfillJobStatus(
            status="completed" if checkpoint.get("completed") else "unknown",
            processed=len(checkpoint.get("processed_ids", [])),
            found=stats.get("found", 0),
            not_found=stats.get("not_found", 0),
            errors=stats.get("errors", 0),
            completed_at=checkpoint.get("last_update"),
        )
    
    return BackfillJobStatus(
        status="no_jobs",
        message="No backfill jobs have been run yet.",
    )


@router.get("/search/{species_name}", response_model=List[ImageSearchResult])
async def search_images_for_species(
    species_name: str,
    sources: Optional[str] = Query(
        default=None,
        description="Comma-separated list of sources to search"
    ),
    limit: int = Query(default=5, ge=1, le=20),
    _api_key: Optional[str] = Depends(require_api_key),
) -> List[ImageSearchResult]:
    """
    Search for images of a species from multiple sources.
    
    This is a live search - results are not cached.
    Use this to preview what images would be found for a species.
    """
    try:
        from mindex_etl.sources.multi_image import MultiSourceImageFetcher
        
        source_list = sources.split(",") if sources else None
        
        async with MultiSourceImageFetcher() as fetcher:
            images = await fetcher.find_all_images(species_name, source_list)
            
            return [
                ImageSearchResult(
                    url=img.url,
                    source=img.source,
                    quality_score=img.quality_score,
                    medium_url=img.medium_url,
                    original_url=img.original_url,
                    photographer=img.photographer,
                )
                for img in images[:limit]
            ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error searching for images: {str(e)}",
        )


@router.post("/backfill/single/{taxon_id}")
async def backfill_single_taxon(
    taxon_id: UUID,
    sources: Optional[str] = Query(
        default=None,
        description="Comma-separated list of sources to search"
    ),
    db: AsyncSession = Depends(get_db),
    _api_key: Optional[str] = Depends(require_api_key),
) -> Dict[str, Any]:
    """
    Backfill image for a single taxon.
    
    Searches for an image and updates the taxon metadata immediately.
    """
    # Get the taxon
    result = await db.execute(
        text("SELECT id, canonical_name, metadata FROM core.taxon WHERE id = :id"),
        {"id": str(taxon_id)},
    )
    row = result.mappings().one_or_none()
    
    if not row:
        raise HTTPException(status_code=404, detail="Taxon not found")
    
    canonical_name = row["canonical_name"]
    
    try:
        from mindex_etl.sources.multi_image import MultiSourceImageFetcher
        
        source_list = sources.split(",") if sources else None
        
        async with MultiSourceImageFetcher() as fetcher:
            image = await fetcher.find_best_image(canonical_name, source_list)
            
            if not image:
                return {
                    "success": False,
                    "taxon_id": str(taxon_id),
                    "canonical_name": canonical_name,
                    "message": "No image found from any source",
                }
            
            # Build photo data
            photo_data = {
                "url": image.url,
                "medium_url": image.medium_url or image.url,
                "original_url": image.original_url or image.url,
                "source": image.source,
                "attribution": image.photographer,
                "license_code": image.license,
                "scraped_at": datetime.now().isoformat(),
                "quality_score": image.quality_score,
            }
            
            # Update the taxon - use cast for asyncpg compatibility
            photo_json = json.dumps(photo_data)
            await db.execute(
                text("""
                    UPDATE core.taxon
                    SET 
                        metadata = jsonb_set(
                            COALESCE(metadata, '{}'::jsonb),
                            '{default_photo}',
                            cast(:photo_data as jsonb),
                            true
                        ),
                        updated_at = NOW()
                    WHERE id = cast(:id as uuid)
                """),
                {"id": str(taxon_id), "photo_data": photo_json},
            )
            await db.commit()
            
            return {
                "success": True,
                "taxon_id": str(taxon_id),
                "canonical_name": canonical_name,
                "image": {
                    "url": image.url,
                    "source": image.source,
                    "quality_score": image.quality_score,
                },
            }
            
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error backfilling image: {str(e)}",
        )
