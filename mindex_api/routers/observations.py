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

# core.taxon has kingdom + fungi_type; iconic_taxon_name lives in observation metadata (iNat).
FUNGI_KINGDOM_SQL = (
    "("
    "lower(coalesce(t.kingdom, '')) LIKE 'fung%%' "
    "OR lower(coalesce(o.metadata->>'kingdom', '')) LIKE 'fung%%' "
    "OR lower(coalesce(o.metadata->>'iconic_taxon_name', '')) = 'fungi' "
    "OR t.fungi_type IS NOT NULL"
    ")"
)

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
    include_total: bool = Query(
        False,
        description="When true, run an exact count. Keep false for low-latency map viewport reads.",
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
        kingdom_norm = kingdom.strip().lower()
        if kingdom_norm in ("fungi", "fungus"):
            where_clauses.append(FUNGI_KINGDOM_SQL)
        else:
            where_clauses.append(
                "("
                "lower(coalesce(t.kingdom, '')) = :kingdom "
                "OR lower(coalesce(o.metadata->>'kingdom', '')) = :kingdom "
                "OR lower(coalesce(o.metadata->>'iconic_taxon_name', '')) = :kingdom"
                ")"
            )
            params["kingdom"] = kingdom_norm
    if start:
        where_clauses.append("o.observed_at >= :start")
        params["start"] = start
    if end:
        where_clauses.append("o.observed_at <= :end")
        params["end"] = end
    if bbox_params:
        where_clauses.append(
            "("
            "(o.location IS NOT NULL "
            "AND ST_Intersects("
            "o.location, "
            "ST_SetSRID(ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat), 4326)::geography"
            ")) "
            "OR (o.location IS NULL "
            "AND COALESCE("
            "NULLIF(to_jsonb(o)->>'latitude', '')::double precision, "
            "(o.metadata->>'latitude')::double precision"
            ") BETWEEN :min_lat AND :max_lat "
            "AND COALESCE("
            "NULLIF(to_jsonb(o)->>'longitude', '')::double precision, "
            "(o.metadata->>'longitude')::double precision"
            ") BETWEEN :min_lon AND :max_lon)"
            ")"
        )
        params.update(bbox_params)

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    # PostGIS-first coordinate projection.
    # Some historical codepaths assumed latitude/longitude columns directly on
    # obs.observation, but canonical schema stores geography in `location`.
    # Reading via to_jsonb(o)->>'latitude' keeps compatibility if those fields
    # are ever present without hard-failing when they are absent.
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
            COALESCE(o.metadata, '{{}}'::jsonb) ||
              jsonb_strip_nulls(jsonb_build_object(
                'taxon_name', t.canonical_name,
                'taxon_common_name', t.common_name,
                'kingdom', t.kingdom,
                'iconic_taxon_name', COALESCE(t.kingdom, o.metadata->>'iconic_taxon_name')
              )) AS metadata,
            COALESCE(
                NULLIF(to_jsonb(o)->>'latitude', '')::double precision,
                CASE WHEN o.location IS NOT NULL THEN ST_Y(o.location::geometry) END
            ) AS latitude,
            COALESCE(
                NULLIF(to_jsonb(o)->>'longitude', '')::double precision,
                CASE WHEN o.location IS NOT NULL THEN ST_X(o.location::geometry) END
            ) AS longitude
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
    total = None
    if include_total:
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


def _kingdom_from_iconic(iconic: Optional[str]) -> Optional[str]:
    value = (iconic or "").strip()
    if not value:
        return None
    if value.lower() in ("fungi", "plantae", "animalia", "bacteria", "archaea", "protista", "viruses"):
        return value
    return value


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

            kingdom = _kingdom_from_iconic(obs.iconic_taxon_name) or obs.metadata.get("kingdom")
            metadata = dict(obs.metadata or {})
            if obs.taxon_name:
                metadata.setdefault("taxon_name", obs.taxon_name)
                metadata.setdefault("scientific_name", obs.taxon_name)
            if obs.taxon_common_name:
                metadata.setdefault("taxon_common_name", obs.taxon_common_name)
                metadata.setdefault("common_name", obs.taxon_common_name)
            if obs.taxon_inat_id:
                metadata.setdefault("taxon_inat_id", obs.taxon_inat_id)
            if obs.iconic_taxon_name:
                metadata.setdefault("iconic_taxon_name", obs.iconic_taxon_name)
                metadata.setdefault("kingdom", kingdom or obs.iconic_taxon_name)

            taxon_id: Optional[str] = None
            if obs.taxon_name:
                taxon_row = await db.execute(
                    text(
                        """
                        SELECT id FROM core.taxon
                        WHERE canonical_name = :canonical_name
                        LIMIT 1
                        """
                    ),
                    {"canonical_name": obs.taxon_name},
                )
                existing_taxon_id = taxon_row.scalar_one_or_none()
                taxon_meta_json = json.dumps(
                    {
                        "source": obs.source,
                        "inat_id": obs.taxon_inat_id,
                        "iconic_taxon_name": obs.iconic_taxon_name,
                    }
                )
                if existing_taxon_id:
                    taxon_id = str(existing_taxon_id)
                    await db.execute(
                        text(
                            """
                            UPDATE core.taxon
                            SET
                                common_name = COALESCE(:common_name, common_name),
                                source = COALESCE(source, :source),
                                kingdom = COALESCE(:kingdom, kingdom),
                                metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:metadata AS jsonb),
                                updated_at = NOW()
                            WHERE id = :taxon_id
                            """
                        ),
                        {
                            "taxon_id": taxon_id,
                            "common_name": obs.taxon_common_name,
                            "source": obs.source,
                            "kingdom": kingdom,
                            "metadata": taxon_meta_json,
                        },
                    )
                else:
                    inserted_taxon = await db.execute(
                        text(
                            """
                            INSERT INTO core.taxon
                                (canonical_name, rank, common_name, source, kingdom, metadata)
                            VALUES
                                (:canonical_name, 'species', :common_name, :source, :kingdom, CAST(:metadata AS jsonb))
                            RETURNING id
                            """
                        ),
                        {
                            "canonical_name": obs.taxon_name,
                            "common_name": obs.taxon_common_name,
                            "source": obs.source,
                            "kingdom": kingdom,
                            "metadata": taxon_meta_json,
                        },
                    )
                    taxon_id = str(inserted_taxon.scalar_one())

            # Check if already exists
            exists = await db.execute(
                text(
                    "SELECT id FROM obs.observation WHERE source = :source AND source_id = :source_id LIMIT 1"
                ),
                {"source": obs.source, "source_id": obs.source_id},
            )
            existing_observation_id = exists.scalar_one_or_none()
            media_json = json.dumps(obs.photos) if obs.photos else "[]"
            meta_json = json.dumps(metadata) if metadata else "{}"

            if existing_observation_id:
                await db.execute(
                    text("""
                        UPDATE obs.observation
                        SET
                            taxon_id = COALESCE(taxon_id, CAST(NULLIF(:taxon_id, '') AS uuid)),
                            observed_at = COALESCE(NULLIF(:observed_at, '')::timestamptz, observed_at),
                            observer = COALESCE(:observer, observer),
                            location = CASE
                              WHEN CAST(:lat AS double precision) IS NOT NULL
                               AND CAST(:lng AS double precision) IS NOT NULL
                              THEN ST_SetSRID(
                                ST_MakePoint(
                                  CAST(:lng AS double precision),
                                  CAST(:lat AS double precision)
                                ),
                                4326
                              )::geography
                              ELSE location
                            END,
                            media = CASE
                              WHEN CAST(:media AS jsonb) <> '[]'::jsonb THEN CAST(:media AS jsonb)
                              ELSE media
                            END,
                            notes = COALESCE(:notes, notes),
                            metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:meta AS jsonb)
                        WHERE id = :id
                    """),
                    {
                        "id": str(existing_observation_id),
                        "taxon_id": taxon_id or "",
                        "observed_at": obs.observed_at,
                        "observer": obs.observer,
                        "lat": obs.lat,
                        "lng": obs.lng,
                        "media": media_json,
                        "notes": obs.notes,
                        "meta": meta_json,
                    },
                )
                skipped += 1
                continue

            obs_id = str(uuid4())

            await db.execute(
                text("""
                    INSERT INTO obs.observation
                        (id, taxon_id, source, source_id, observed_at, observer,
                         location, media, notes, metadata)
                    VALUES
                        (:id, CAST(NULLIF(:taxon_id, '') AS uuid), :source, :source_id,
                         COALESCE(NULLIF(:observed_at, '')::timestamptz, NOW()),
                         :observer,
                         CASE
                           WHEN CAST(:lat AS double precision) IS NOT NULL
                            AND CAST(:lng AS double precision) IS NOT NULL
                           THEN ST_SetSRID(
                             ST_MakePoint(
                               CAST(:lng AS double precision),
                               CAST(:lat AS double precision)
                             ),
                             4326
                           )::geography
                           ELSE NULL
                         END,
                         cast(:media as jsonb), :notes, cast(:meta as jsonb))
                """),
                {
                    "id": obs_id,
                    "taxon_id": taxon_id or "",
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
            # Roll back failed statement so subsequent rows can continue.
            await db.rollback()
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
