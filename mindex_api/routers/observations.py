from __future__ import annotations

import json
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import (
    PaginationParams,
    get_db_session,
    pagination_params,
    require_api_key,
)
from ..contracts.v1.observations import ObservationListResponse

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
        WHERE {where_sql}
        ORDER BY o.observed_at DESC
        LIMIT :limit OFFSET :offset
    """
    count_query = f"""
        SELECT count(*) FROM obs.observation o
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

    return ObservationListResponse(
        data=observations,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        },
    )
