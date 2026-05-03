from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import (
    PaginationParams,
    get_db_session,
    pagination_params,
    require_api_key,
)
from ..contracts.v1.observations import ObservationListResponse
from ..utils.deep_agent_events import schedule_domain_event

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/observations",
    tags=["observations"],
    dependencies=[Depends(require_api_key)],
)


def _parse_bbox(bbox: Optional[str]) -> Optional[dict]:
    if not bbox:
        return None
    try:
        parts = [float(x.strip()) for x in bbox.split(",")]
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid bbox format") from exc
    if len(parts) != 4:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expected bbox=minLon,minLat,maxLon,maxLat")
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid bbox coordinates")
    return {
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
    }


@router.get("", response_model=ObservationListResponse)
async def list_observations(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    taxon_id: Optional[UUID] = Query(None, description="Filter observations by taxon id."),
    kingdom: Optional[str] = Query(
        None,
        description="Filter by taxon kingdom (joins core.taxon). Omit for all.",
    ),
    start: Optional[datetime] = Query(None, description="ISO timestamp lower bound."),
    end: Optional[datetime] = Query(None, description="ISO timestamp upper bound."),
    bbox: Optional[str] = Query(
        None,
        description="Bounding box filter minLon,minLat,maxLon,maxLat in WGS84.",
    ),
) -> ObservationListResponse:
    bbox_params = _parse_bbox(bbox)

    # Build dynamic WHERE clause to avoid asyncpg NULL parameter issues
    where_clauses = []
    params: dict = {
        "limit": pagination.limit,
        "offset": pagination.offset,
    }

    if taxon_id:
        where_clauses.append("o.taxon_id = :taxon_id")
        params["taxon_id"] = str(taxon_id)
    if kingdom and kingdom.strip().lower() not in ("all", "any", ""):
        where_clauses.append("t.kingdom = :kingdom")
        params["kingdom"] = kingdom.strip()
    if start:
        where_clauses.append("o.observed_at >= :start")
        params["start"] = start
    if end:
        where_clauses.append("o.observed_at <= :end")
        params["end"] = end
    if bbox_params:
        where_clauses.append(
            "o.latitude IS NOT NULL AND o.longitude IS NOT NULL "
            "AND o.latitude BETWEEN :min_lat AND :max_lat AND o.longitude BETWEEN :min_lon AND :max_lon"
        )
        params.update(bbox_params)

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    # Support both PostGIS (location) and plain lat/lng schema
    data_query = f"""
        SELECT
            o.id,
            o.taxon_id,
            o.source,
            o.source_id,
            o.observer,
            o.observed_at,
            o.accuracy_m,
            o.media,
            o.notes,
            o.metadata,
            o.latitude,
            o.longitude
        FROM obs.observation o
        LEFT JOIN core.taxon t ON t.id = o.taxon_id
        WHERE {where_sql}
        ORDER BY o.observed_at DESC
        LIMIT :limit OFFSET :offset
    """
    count_query = f"""
        SELECT count(*) FROM obs.observation o
        LEFT JOIN core.taxon t ON t.id = o.taxon_id
        WHERE {where_sql}
    """

    result = await db.execute(text(data_query), params)
    count_result = await db.execute(text(count_query), params)
    total = count_result.scalar_one()

    observations = []
    for row in result.mappings().all():
        data = dict(row)
        lat = data.pop("latitude", None)
        lng = data.pop("longitude", None)
        if lat is not None and lng is not None:
            data["location"] = {"type": "Point", "coordinates": [float(lng), float(lat)]}
        else:
            data["location"] = None
        observations.append(data)

    schedule_domain_event(
        domain="search",
        task="MINDEX observations list requested",
        context={
            "route": "/observations",
            "taxon_id": str(taxon_id) if taxon_id else None,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "total": total,
            "limit": pagination.limit,
            "offset": pagination.offset,
        },
        preferred_agent="myca-research",
    )
    return ObservationListResponse(
        data=observations,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# BULK INGEST — Clone-on-Display endpoint
# ═══════════════════════════════════════════════════════════════════════════


class BulkObservationItem(BaseModel):
    source: str = "inat"
    source_id: Optional[str] = None
    observed_at: Optional[str] = None
    observer: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    taxon_name: Optional[str] = None
    taxon_common_name: Optional[str] = None
    taxon_inat_id: Optional[int] = None
    iconic_taxon_name: Optional[str] = None
    photos: List[dict] = Field(default_factory=list)
    notes: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class BulkIngestRequest(BaseModel):
    observations: List[BulkObservationItem]


class BulkIngestResponse(BaseModel):
    inserted: int = 0
    skipped: int = 0
    errors: int = 0


@router.post("/bulk", response_model=BulkIngestResponse)
async def bulk_ingest_observations(
    body: BulkIngestRequest = Body(...),
    db: AsyncSession = Depends(get_db_session),
) -> BulkIngestResponse:
    """Bulk upsert observations from clone-on-display or external scrapers.

    Deduplicates on (source, source_id) — existing rows are skipped.
    """
    inserted = 0
    skipped = 0
    errors = 0

    for obs in body.observations:
        try:
            if not obs.source_id:
                errors += 1
                continue

            # Check if already exists
            exists = await db.execute(
                text(
                    "SELECT 1 FROM obs.observation WHERE source = :source AND source_id = :source_id LIMIT 1"
                ),
                {"source": obs.source, "source_id": obs.source_id},
            )
            if exists.scalar_one_or_none():
                skipped += 1
                continue

            obs_id = str(uuid4())
            media_json = json.dumps(obs.photos) if obs.photos else "[]"
            meta_json = json.dumps(obs.metadata) if obs.metadata else "{}"

            await db.execute(
                text("""
                    INSERT INTO obs.observation
                        (id, source, source_id, observed_at, observer,
                         latitude, longitude, media, notes, metadata)
                    VALUES
                        (:id, :source, :source_id, :observed_at, :observer,
                         :lat, :lng, cast(:media as jsonb), :notes, cast(:meta as jsonb))
                """),
                {
                    "id": obs_id,
                    "source": obs.source,
                    "source_id": obs.source_id,
                    "observed_at": obs.observed_at,
                    "observer": obs.observer,
                    "lat": obs.lat,
                    "lng": obs.lng,
                    "media": media_json,
                    "notes": obs.notes,
                    "meta": meta_json,
                },
            )
            inserted += 1
        except Exception as exc:
            logger.warning("Bulk ingest error for source_id=%s: %s", obs.source_id, exc)
            errors += 1

    await db.commit()
    logger.info("Bulk ingest complete: inserted=%d skipped=%d errors=%d", inserted, skipped, errors)
    schedule_domain_event(
        domain="search",
        task="MINDEX observations bulk ingest completed",
        context={
            "route": "/observations/bulk",
            "inserted": inserted,
            "skipped": skipped,
            "errors": errors,
            "submitted_count": len(body.observations),
        },
        preferred_agent="myca-research",
    )
    return BulkIngestResponse(inserted=inserted, skipped=skipped, errors=errors)
