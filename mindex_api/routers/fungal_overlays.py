from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, require_api_key

router = APIRouter(
    prefix="/fungal-overlays",
    tags=["fungal-overlays"],
    dependencies=[Depends(require_api_key)],
)


class FungalOverlayCell(BaseModel):
    cell_id: str
    centroid_lat: float
    centroid_lng: float
    resolution_deg: float
    observation_count: int
    ecm_density: float
    am_density: float
    fungi_intensity: float
    uncertainty: float
    rarity: float
    protected_weight: float
    humidity_suitability: float
    moisture_suitability: float
    temperature_suitability: float
    sample_coverage: float
    mycelium_heat: float
    atlas_class: str
    fci_priority: float
    observed_from: Optional[datetime] = None
    observed_to: Optional[datetime] = None


class FungalOverlayCellsResponse(BaseModel):
    data: list[FungalOverlayCell]
    meta: dict[str, Any]


class FungalOverlaySample(BaseModel):
    id: str
    lat: float
    lng: float
    observed_at: Optional[datetime] = None
    source: Optional[str] = None
    species: Optional[str] = None
    group: str
    confidence: float
    ecm_score: float
    am_score: float


class FungalOverlaySamplesResponse(BaseModel):
    data: list[FungalOverlaySample]
    meta: dict[str, Any]


class FciScoreWeights(BaseModel):
    ecm_density: float = 0.16
    am_density: float = 0.14
    fungi_intensity: float = 0.22
    rarity: float = 0.14
    protected_weight: float = 0.10
    humidity_suitability: float = 0.08
    moisture_suitability: float = 0.08
    temperature_suitability: float = 0.08


class FciDeploymentResult(BaseModel):
    rank: int
    cell_id: str
    centroid_lat: float
    centroid_lng: float
    fci_priority: float
    mycelium_heat: float
    atlas_class: str
    deployment_score: float
    explain: dict[str, Any]


def _parse_bbox(bbox: Optional[str]) -> Optional[dict[str, float]]:
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


def _clamp01(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return float(value)


def _atlas_class_for_row(row: dict[str, Any]) -> str:
    count = float(row.get("observation_count") or 0.0)
    rarity = float(row.get("rarity") or 0.0)
    if count >= 50 and rarity >= 0.75:
        return "hotspot_rare"
    if count >= 40:
        return "hotspot"
    if count >= 15:
        return "active"
    return "sparse"


def _compute_fci_priority(row: dict[str, Any], weights: FciScoreWeights) -> float:
    score = (
        float(row.get("fungi_intensity") or 0.0) * weights.fungi_intensity
        + float(row.get("ecm_density") or 0.0) * weights.ecm_density
        + float(row.get("am_density") or 0.0) * weights.am_density
        + float(row.get("rarity") or 0.0) * weights.rarity
        + float(row.get("protected_weight") or 0.0) * weights.protected_weight
        + float(row.get("humidity_suitability") or 0.0) * weights.humidity_suitability
        + float(row.get("moisture_suitability") or 0.0) * weights.moisture_suitability
        + float(row.get("temperature_suitability") or 0.0) * weights.temperature_suitability
    )
    return _clamp01(score)


@router.get("/cells", response_model=FungalOverlayCellsResponse)
async def get_fungal_overlay_cells(
    db: AsyncSession = Depends(get_db_session),
    layer: str = Query("mycelium", description="mycelium|am|ecm|rarity|fci|samples"),
    bbox: Optional[str] = Query(None, description="Bounding box minLon,minLat,maxLon,maxLat"),
    limit: int = Query(1200, ge=1, le=20000),
    resolution_deg: float = Query(0.25, ge=0.01, le=5.0),
) -> FungalOverlayCellsResponse:
    bbox_params = _parse_bbox(bbox)
    params: dict[str, Any] = {
        "limit": limit,
        "resolution_deg": resolution_deg,
    }

    where_sql = "o.location IS NOT NULL"
    if bbox_params:
        where_sql += (
            " AND ST_Y(o.location::geometry) BETWEEN :min_lat AND :max_lat"
            " AND ST_X(o.location::geometry) BETWEEN :min_lon AND :max_lon"
        )
        params.update(bbox_params)

    stmt = text(
        f"""
        WITH fungi_obs AS (
            SELECT
                o.id,
                o.observed_at,
                o.source,
                ST_Y(o.location::geometry) AS lat,
                ST_X(o.location::geometry) AS lng,
                lower(
                    coalesce(
                        t.canonical_name,
                        t.common_name,
                        o.metadata->>'scientific_name',
                        o.metadata->>'species',
                        ''
                    )
                ) AS species_name,
                lower(coalesce(t.kingdom, o.metadata->>'kingdom', '')) AS kingdom_name,
                CASE
                    WHEN (o.metadata->>'humidity') ~ '^-?\\d+(\\.\\d+)?$' THEN (o.metadata->>'humidity')::double precision
                    WHEN (o.metadata#>>'{{weather,humidity}}') ~ '^-?\\d+(\\.\\d+)?$' THEN (o.metadata#>>'{{weather,humidity}}')::double precision
                    ELSE NULL
                END AS humidity,
                CASE
                    WHEN (o.metadata->>'moisture') ~ '^-?\\d+(\\.\\d+)?$' THEN (o.metadata->>'moisture')::double precision
                    WHEN (o.metadata#>>'{{weather,precipitation}}') ~ '^-?\\d+(\\.\\d+)?$' THEN (o.metadata#>>'{{weather,precipitation}}')::double precision
                    ELSE NULL
                END AS moisture,
                CASE
                    WHEN (o.metadata->>'temperature') ~ '^-?\\d+(\\.\\d+)?$' THEN (o.metadata->>'temperature')::double precision
                    WHEN (o.metadata#>>'{{weather,temperature}}') ~ '^-?\\d+(\\.\\d+)?$' THEN (o.metadata#>>'{{weather,temperature}}')::double precision
                    ELSE NULL
                END AS temperature,
                CASE
                    WHEN lower(coalesce(o.metadata->>'protected_area', 'false')) IN ('1', 'true', 'yes') THEN 1.0
                    ELSE 0.0
                END AS protected_weight
            FROM obs.observation o
            LEFT JOIN core.taxon t ON t.id = o.taxon_id
            WHERE {where_sql}
              AND (
                lower(coalesce(t.kingdom, '')) LIKE 'fung%%'
                OR lower(coalesce(o.metadata->>'kingdom', '')) LIKE 'fung%%'
                OR lower(coalesce(o.metadata->>'iconic_taxon_name', '')) = 'fungi'
                OR t.fungi_type IS NOT NULL
              )
        ),
        bucketed AS (
            SELECT
                floor((lat + 90.0) / :resolution_deg)::int AS y_idx,
                floor((lng + 180.0) / :resolution_deg)::int AS x_idx,
                min(lat) AS min_lat,
                max(lat) AS max_lat,
                min(lng) AS min_lng,
                max(lng) AS max_lng,
                count(*)::int AS observation_count,
                avg(
                    CASE
                        WHEN species_name LIKE '%%ectomyc%%'
                          OR species_name LIKE '%%bolet%%'
                          OR species_name LIKE '%%amanit%%'
                          OR species_name LIKE '%%truffle%%'
                        THEN 1.0 ELSE 0.15
                    END
                )::double precision AS ecm_density,
                avg(
                    CASE
                        WHEN species_name LIKE '%%arbuscular%%'
                          OR species_name LIKE '%%glomer%%'
                          OR species_name LIKE '%%rhizophagus%%'
                        THEN 1.0 ELSE 0.15
                    END
                )::double precision AS am_density,
                avg(
                    CASE
                        WHEN species_name LIKE '%%truffle%%'
                          OR species_name LIKE '%%cordyceps%%'
                          OR species_name LIKE '%%hericium%%'
                          OR species_name LIKE '%%morchella%%'
                        THEN 0.92 ELSE 0.38
                    END
                )::double precision AS rarity,
                avg(protected_weight)::double precision AS protected_weight,
                avg(
                    CASE
                        WHEN humidity IS NULL THEN 0.45
                        WHEN humidity BETWEEN 55 AND 95 THEN 1.0
                        WHEN humidity BETWEEN 40 AND 55 THEN 0.7
                        WHEN humidity BETWEEN 95 AND 110 THEN 0.6
                        ELSE 0.35
                    END
                )::double precision AS humidity_suitability,
                avg(
                    CASE
                        WHEN moisture IS NULL THEN 0.45
                        WHEN moisture BETWEEN 20 AND 80 THEN 1.0
                        WHEN moisture BETWEEN 10 AND 20 THEN 0.75
                        WHEN moisture BETWEEN 80 AND 95 THEN 0.65
                        ELSE 0.35
                    END
                )::double precision AS moisture_suitability,
                avg(
                    CASE
                        WHEN temperature IS NULL THEN 0.5
                        WHEN temperature BETWEEN 6 AND 28 THEN 1.0
                        WHEN temperature BETWEEN -2 AND 6 THEN 0.7
                        WHEN temperature BETWEEN 28 AND 34 THEN 0.65
                        ELSE 0.3
                    END
                )::double precision AS temperature_suitability,
                min(observed_at) AS observed_from,
                max(observed_at) AS observed_to
            FROM fungi_obs
            GROUP BY 1, 2
        )
        SELECT
            concat('cell:', y_idx::text, ':', x_idx::text) AS cell_id,
            ((y_idx * :resolution_deg) - 90.0 + (:resolution_deg / 2.0))::double precision AS centroid_lat,
            ((x_idx * :resolution_deg) - 180.0 + (:resolution_deg / 2.0))::double precision AS centroid_lng,
            :resolution_deg::double precision AS resolution_deg,
            observation_count,
            ecm_density,
            am_density,
            LEAST(1.0, observation_count::double precision / 120.0)::double precision AS fungi_intensity,
            GREATEST(0.05, 1.0 - LEAST(1.0, ln(observation_count + 1) / ln(120.0)))::double precision AS uncertainty,
            rarity,
            protected_weight,
            humidity_suitability,
            moisture_suitability,
            temperature_suitability,
            LEAST(1.0, observation_count::double precision / 60.0)::double precision AS sample_coverage,
            LEAST(1.0, (ecm_density + am_density + LEAST(1.0, observation_count::double precision / 120.0)) / 3.0)::double precision AS mycelium_heat,
            CASE
                WHEN observation_count >= 50 AND rarity >= 0.75 THEN 'hotspot_rare'
                WHEN observation_count >= 40 THEN 'hotspot'
                WHEN observation_count >= 15 THEN 'active'
                ELSE 'sparse'
            END AS atlas_class,
            LEAST(
                1.0,
                (
                    (LEAST(1.0, observation_count::double precision / 120.0) * 0.22)
                    + (ecm_density * 0.16)
                    + (am_density * 0.14)
                    + (rarity * 0.14)
                    + (protected_weight * 0.1)
                    + (humidity_suitability * 0.08)
                    + (moisture_suitability * 0.08)
                    + (temperature_suitability * 0.08)
                )
            )::double precision AS fci_priority,
            observed_from,
            observed_to
        FROM bucketed
        ORDER BY
            CASE
                WHEN :layer = 'am' THEN am_density
                WHEN :layer = 'ecm' THEN ecm_density
                WHEN :layer = 'rarity' THEN rarity
                WHEN :layer = 'fci' THEN LEAST(
                    1.0,
                    (
                        (LEAST(1.0, observation_count::double precision / 120.0) * 0.22)
                        + (ecm_density * 0.16)
                        + (am_density * 0.14)
                        + (rarity * 0.14)
                        + (protected_weight * 0.1)
                        + (humidity_suitability * 0.08)
                        + (moisture_suitability * 0.08)
                        + (temperature_suitability * 0.08)
                    )
                )
                ELSE LEAST(1.0, (ecm_density + am_density + LEAST(1.0, observation_count::double precision / 120.0)) / 3.0)
            END DESC,
            observation_count DESC
        LIMIT :limit
        """
    )
    params["layer"] = layer
    result = await db.execute(stmt, params)
    rows = [dict(row) for row in result.mappings().all()]
    weights = FciScoreWeights()
    for row in rows:
        row["mycelium_heat"] = _clamp01(float(row.get("mycelium_heat") or 0.0))
        row["fci_priority"] = _compute_fci_priority(row, weights)
        row["atlas_class"] = _atlas_class_for_row(row)
    return FungalOverlayCellsResponse(
        data=rows,
        meta={
            "layer": layer,
            "bbox": bbox_params,
            "resolution_deg": resolution_deg,
            "count": len(rows),
            "source": "mindex.obs.observation",
            "scoring_weights": weights.model_dump(),
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


@router.get("/samples", response_model=FungalOverlaySamplesResponse)
async def get_fungal_overlay_samples(
    db: AsyncSession = Depends(get_db_session),
    bbox: Optional[str] = Query(None, description="Bounding box minLon,minLat,maxLon,maxLat"),
    limit: int = Query(5000, ge=1, le=20000),
) -> FungalOverlaySamplesResponse:
    bbox_params = _parse_bbox(bbox)
    params: dict[str, Any] = {"limit": limit}
    where_sql = "o.location IS NOT NULL"
    if bbox_params:
        where_sql += (
            " AND ST_Y(o.location::geometry) BETWEEN :min_lat AND :max_lat"
            " AND ST_X(o.location::geometry) BETWEEN :min_lon AND :max_lon"
        )
        params.update(bbox_params)
    fungi_where = (
        " AND ("
        "lower(coalesce(t.kingdom, '')) LIKE 'fung%%' "
        "OR lower(coalesce(o.metadata->>'kingdom', '')) LIKE 'fung%%' "
        "OR lower(coalesce(o.metadata->>'iconic_taxon_name', '')) = 'fungi' "
        "OR t.fungi_type IS NOT NULL"
        ")"
    )

    stmt = text(
        f"""
        SELECT
            o.id::text AS id,
            ST_Y(o.location::geometry)::double precision AS lat,
            ST_X(o.location::geometry)::double precision AS lng,
            o.observed_at,
            o.source,
            coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species') AS species,
            CASE
                WHEN lower(coalesce(t.kingdom, o.metadata->>'kingdom', '')) LIKE 'fung%%'
                  OR lower(coalesce(o.metadata->>'iconic_taxon_name', '')) = 'fungi'
                  OR t.fungi_type IS NOT NULL
                THEN
                  CASE
                    WHEN lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%arbuscular%%'
                      OR lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%glomer%%'
                    THEN 'am'
                    WHEN lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%ectomyc%%'
                      OR lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%bolet%%'
                      OR lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%amanit%%'
                    THEN 'ecm'
                    ELSE 'fungi'
                  END
                ELSE 'unknown'
            END AS "group",
            CASE
                WHEN o.source = 'inat' THEN 0.85
                WHEN o.source = 'gbif' THEN 0.78
                ELSE 0.7
            END::double precision AS confidence,
            CASE
                WHEN lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%ectomyc%%'
                  OR lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%bolet%%'
                  OR lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%amanit%%'
                THEN 1.0 ELSE 0.2
            END::double precision AS ecm_score,
            CASE
                WHEN lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%arbuscular%%'
                  OR lower(coalesce(t.canonical_name, t.common_name, o.metadata->>'scientific_name', o.metadata->>'species', '')) LIKE '%%glomer%%'
                THEN 1.0 ELSE 0.2
            END::double precision AS am_score
        FROM obs.observation o
        LEFT JOIN core.taxon t ON t.id = o.taxon_id
        WHERE {where_sql}{fungi_where}
        ORDER BY o.observed_at DESC NULLS LAST
        LIMIT :limit
        """
    )
    result = await db.execute(stmt, params)
    rows = [dict(row) for row in result.mappings().all()]
    return FungalOverlaySamplesResponse(
        data=rows,
        meta={
            "bbox": bbox_params,
            "count": len(rows),
            "source": "mindex.obs.observation",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


@router.get("/deployment/land")
async def get_land_deployment_ranking(
    db: AsyncSession = Depends(get_db_session),
    bbox: str = Query(..., description="Bounding box minLon,minLat,maxLon,maxLat"),
    limit: int = Query(20, ge=1, le=200),
    mission: str = Query("mushroom1-fci", description="Mission profile identifier"),
) -> dict[str, Any]:
    cells = await get_fungal_overlay_cells(
        db=db,
        layer="fci",
        bbox=bbox,
        limit=max(200, limit * 5),
        resolution_deg=0.25,
    )
    weights = FciScoreWeights()
    ranked: list[FciDeploymentResult] = []
    for row in cells.data:
        deployment_score = _clamp01(
            row.fci_priority * 0.56
            + row.mycelium_heat * 0.22
            + row.sample_coverage * 0.14
            + row.protected_weight * 0.08
        )
        explain = {
            "weights": weights.model_dump(),
            "factors": {
                "ecm_density": row.ecm_density,
                "am_density": row.am_density,
                "fungi_intensity": row.fungi_intensity,
                "rarity": row.rarity,
                "protected_weight": row.protected_weight,
                "humidity_suitability": row.humidity_suitability,
                "moisture_suitability": row.moisture_suitability,
                "temperature_suitability": row.temperature_suitability,
                "sample_coverage": row.sample_coverage,
                "mycelium_heat": row.mycelium_heat,
            },
            "mission": mission,
        }
        ranked.append(
            FciDeploymentResult(
                rank=0,
                cell_id=row.cell_id,
                centroid_lat=row.centroid_lat,
                centroid_lng=row.centroid_lng,
                fci_priority=row.fci_priority,
                mycelium_heat=row.mycelium_heat,
                atlas_class=row.atlas_class,
                deployment_score=deployment_score,
                explain=explain,
            )
        )

    ranked.sort(key=lambda item: item.deployment_score, reverse=True)
    for idx, item in enumerate(ranked[:limit], start=1):
        item.rank = idx

    return {
        "mission": mission,
        "generated_at": datetime.utcnow().isoformat(),
        "count": min(limit, len(ranked)),
        "results": [item.model_dump() for item in ranked[:limit]],
    }


@router.get("/health")
async def get_fungal_overlay_health(
    db: AsyncSession = Depends(get_db_session),
    lag_minutes_threshold: int = Query(180, ge=5, le=1440),
    min_recent_observations: int = Query(100, ge=1, le=200000),
) -> dict[str, Any]:
    recency_stmt = text(
        """
        SELECT
            count(*)::int AS total_count,
            count(*) FILTER (WHERE observed_at >= now() - interval '24 hours')::int AS recent_24h_count,
            max(observed_at) AS latest_observed_at
        FROM obs.observation o
        LEFT JOIN core.taxon t ON t.id = o.taxon_id
        WHERE o.location IS NOT NULL
          AND (
            lower(coalesce(t.kingdom, '')) LIKE 'fung%'
            OR lower(coalesce(o.metadata->>'kingdom', '')) LIKE 'fung%'
          )
        """
    )
    recency = dict((await db.execute(recency_stmt)).mappings().first() or {})
    latest = recency.get("latest_observed_at")
    lag_minutes = None
    if latest:
        lag_minutes = int((datetime.utcnow() - latest.replace(tzinfo=None)).total_seconds() / 60)

    recent_count = int(recency.get("recent_24h_count") or 0)
    total_count = int(recency.get("total_count") or 0)
    has_recent_data = recent_count >= min_recent_observations
    lag_ok = lag_minutes is not None and lag_minutes <= lag_minutes_threshold
    confidence = _clamp01(
        (0.5 if has_recent_data else 0.0)
        + (0.5 if lag_ok else 0.0)
    )
    healthy = has_recent_data and lag_ok

    return {
        "healthy": healthy,
        "mindex_first_confidence": confidence,
        "metrics": {
            "total_fungi_observations": total_count,
            "recent_24h_observations": recent_count,
            "latest_observed_at": latest.isoformat() if latest else None,
            "lag_minutes": lag_minutes,
        },
        "thresholds": {
            "lag_minutes_threshold": lag_minutes_threshold,
            "min_recent_observations": min_recent_observations,
        },
        "timestamp": datetime.utcnow().isoformat(),
    }
