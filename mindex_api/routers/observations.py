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


def _build_query(include_bbox: bool, base: str) -> str:
    bbox_clause = (
        """
        AND ST_Intersects(
            o.location::geometry,
            ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
        )
        """
        if include_bbox
        else ""
    )
    return base + bbox_clause


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
    base_where = """
        FROM obs.observation o
        WHERE (:taxon_id::uuid IS NULL OR o.taxon_id = :taxon_id)
          AND (:start IS NULL OR o.observed_at >= :start)
          AND (:end IS NULL OR o.observed_at <= :end)
    """
    data_query = _build_query(
        bool(bbox_params),
        f"""
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
            ST_AsGeoJSON(o.location::geometry) AS location_geojson
        {base_where}
        ORDER BY o.observed_at DESC
        LIMIT :limit OFFSET :offset
        """,
    )
    count_query = _build_query(
        bool(bbox_params),
        f"""
        SELECT count(*) {base_where}
        """,
    )

    params = {
        "taxon_id": str(taxon_id) if taxon_id else None,
        "start": start,
        "end": end,
        "limit": pagination.limit,
        "offset": pagination.offset,
    }
    if bbox_params:
        params.update(bbox_params)

    result = await db.execute(text(data_query), params)
    count_result = await db.execute(text(count_query), params)
    total = count_result.scalar_one()

    observations = []
    for row in result.mappings().all():
        data = dict(row)
        loc = data.pop("location_geojson", None)
        data["location"] = json.loads(loc) if loc else None
        observations.append(data)

    return ObservationListResponse(
        data=observations,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        },
    )
