"""
All-life ancestry: kingdom stats, interactions, media, publications, lineage tree.
Requires migration 20260502_all_life_universal.sql (bio.taxon_full, bio.taxon_interaction, etc.).
"""
from __future__ import annotations

from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, require_api_key

router = APIRouter(
    prefix="/all-life",
    tags=["all-life"],
    dependencies=[Depends(require_api_key)],
)


async def _require_relation_columns(
    db: AsyncSession,
    *,
    schema: str,
    table: str,
    required: set[str],
) -> None:
    """Fail closed when an all-life projection's installed schema is incomplete."""
    try:
        result = await db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": schema, "table": table},
        )
        present = {row[0] for row in result.fetchall()}
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "schema_check_unavailable", "message": f"{schema}.{table} readiness could not be verified"},
        ) from exc
    if not required.issubset(present):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "schema_incomplete", "message": f"{schema}.{table} projection is unavailable"},
        )


class KingdomStatsRow(BaseModel):
    kingdom: str
    taxon_count: int


@router.get("/kingdom-stats", response_model=List[KingdomStatsRow])
async def kingdom_stats(db: AsyncSession = Depends(get_db_session)) -> List[KingdomStatsRow]:
    r = await db.execute(text("SELECT kingdom, taxon_count FROM bio.kingdom_stats ORDER BY taxon_count DESC"))
    return [KingdomStatsRow(kingdom=row[0], taxon_count=row[1]) for row in r.fetchall()]


@router.get("/taxa/{taxon_id}/interactions")
async def list_interactions(
    taxon_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(200, le=2000, ge=1),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    await _require_relation_columns(
        db, schema="bio", table="taxon_interaction",
        required={"id", "source_taxon_id", "target_taxon_id", "interaction_type", "evidence_source", "evidence_url", "location", "metadata", "created_at"},
    )
    try:
        r = await db.execute(
            text(
                """
                SELECT id, source_taxon_id, target_taxon_id, interaction_type::text AS interaction_type,
                       evidence_source, evidence_url, ST_AsGeoJSON(location)::json AS location,
                       metadata, created_at
                FROM bio.taxon_interaction
                WHERE source_taxon_id = :id OR target_taxon_id = :id
                ORDER BY created_at DESC, id
                LIMIT :lim OFFSET :off
                """
            ),
            {"id": str(taxon_id), "lim": limit, "off": offset},
        )
        rows = [dict(x) for x in r.mappings().all()]
        c = await db.execute(
            text(
                "SELECT count(*) FROM bio.taxon_interaction "
                "WHERE source_taxon_id = :id OR target_taxon_id = :id"
            ),
            {"id": str(taxon_id)},
        )
        total = int(c.scalar_one())
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "interaction_query_unavailable", "message": "Stored interaction records could not be read"},
        ) from exc
    return {
        "taxon_id": str(taxon_id), "data": rows,
        "data_state": "available", "schema_state": "ready", "scope": "exact_taxon_either_direction",
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


@router.get("/taxa/{taxon_id}/media")
async def list_media(
    taxon_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(100, le=500, ge=1),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    required_image_columns = {
        "taxon_id", "mindex_id", "filename", "source", "source_url", "license",
        "attribution", "source_id", "species_confidence", "species_match_method",
        "verified", "label_state", "content_hash", "created_at",
    }
    try:
        columns_result = await db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'media' AND table_name = 'image'"
            )
        )
        image_columns = {row[0] for row in columns_result.fetchall()}
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "image_schema_unavailable", "message": "Image schema readiness could not be verified"},
        ) from exc
    missing_image_columns = sorted(required_image_columns - image_columns)
    if missing_image_columns:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "image_schema_incomplete", "message": "Image attribution/linkage schema is unavailable"},
        )

    try:
        image_result = await db.execute(
            text(
                """
                SELECT id, taxon_id, mindex_id, filename, source, source_id, source_url,
                       license, attribution, species_confidence, species_match_method,
                       verified, label_state::text AS label_state, content_hash, created_at
                FROM media.image
                WHERE taxon_id = :id
                ORDER BY verified DESC, species_confidence DESC NULLS LAST, created_at DESC, id
                LIMIT :limit OFFSET :offset
                """
            ),
            {"id": str(taxon_id), "limit": limit, "offset": offset},
        )
        images = [dict(row) for row in image_result.mappings().all()]
        image_count_result = await db.execute(
            text("SELECT count(*) FROM media.image WHERE taxon_id = :id"),
            {"id": str(taxon_id)},
        )
        image_total = int(image_count_result.scalar_one())
        v = await db.execute(
            text("SELECT * FROM media.video WHERE taxon_id = :id ORDER BY created_at DESC, id LIMIT :limit OFFSET :offset"),
            {"id": str(taxon_id), "limit": limit, "offset": offset},
        )
        videos = [dict(row) for row in v.mappings().all()]
        video_count_result = await db.execute(
            text("SELECT count(*) FROM media.video WHERE taxon_id = :id"), {"id": str(taxon_id)},
        )
        video_total = int(video_count_result.scalar_one())
        a = await db.execute(
            text("SELECT * FROM media.audio WHERE taxon_id = :id ORDER BY created_at DESC, id LIMIT :limit OFFSET :offset"),
            {"id": str(taxon_id), "limit": limit, "offset": offset},
        )
        audio = [dict(row) for row in a.mappings().all()]
        audio_count_result = await db.execute(
            text("SELECT count(*) FROM media.audio WHERE taxon_id = :id"), {"id": str(taxon_id)},
        )
        audio_total = int(audio_count_result.scalar_one())
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "media_query_unavailable", "message": "Stored species media could not be read"},
        ) from exc

    return {
        "taxon_id": str(taxon_id),
        "image": images,
        "image_state": "available",
        "image_pagination": {"limit": limit, "offset": offset, "total": image_total},
        "video": videos,
        "video_state": "available",
        "video_pagination": {"limit": limit, "offset": offset, "total": video_total},
        "audio": audio,
        "audio_state": "available",
        "audio_pagination": {"limit": limit, "offset": offset, "total": audio_total},
    }


@router.get("/taxa/{taxon_id}/publications")
async def list_publications(
    taxon_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(200, le=2000, ge=1),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    await _require_relation_columns(
        db, schema="bio", table="publication_taxon",
        required={"publication_id", "taxon_id", "relevance_score", "created_at"},
    )
    await _require_relation_columns(
        db, schema="core", table="publications", required={"id"},
    )
    try:
        r = await db.execute(
            text(
                """
                SELECT p.*, pt.relevance_score, pt.created_at AS linked_at
                FROM bio.publication_taxon pt
                JOIN core.publications p ON p.id = pt.publication_id
                WHERE pt.taxon_id = :id
                ORDER BY pt.relevance_score DESC NULLS LAST, pt.created_at DESC, pt.publication_id
                LIMIT :lim OFFSET :off
                """
            ),
            {"id": str(taxon_id), "lim": limit, "off": offset},
        )
        rows = [dict(x) for x in r.mappings().all()]
        c = await db.execute(
            text("SELECT count(*) FROM bio.publication_taxon WHERE taxon_id = :id"),
            {"id": str(taxon_id)},
        )
        total = int(c.scalar_one())
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "publication_query_unavailable", "message": "Stored publication links could not be read"},
        ) from exc
    return {
        "taxon_id": str(taxon_id), "data": rows,
        "data_state": "available", "schema_state": "ready", "scope": "exact_taxon",
        "association_provenance_state": "not_recorded_by_current_link_schema",
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


@router.get("/taxa/{taxon_id}/characteristics")
async def list_characteristics(
    taxon_id: UUID,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    r = await db.execute(
        text(
            "SELECT * FROM bio.taxon_characteristic WHERE taxon_id = :id ORDER BY name, created_at DESC"
        ),
        {"id": str(taxon_id)},
    )
    return {"data": [dict(x) for x in r.mappings().all()]}


@router.get("/taxa/{taxon_id}/lineage-tree", response_model=dict)
async def lineage_tree(
    taxon_id: UUID,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    r = await db.execute(
        text("SELECT lineage, lineage_ids, kingdom, canonical_name FROM core.taxon WHERE id = :id"),
        {"id": str(taxon_id)},
    )
    row = r.mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Taxon not found")
    names: list[str] = list(row["lineage"] or [])
    lids: list[Any] = list(row["lineage_ids"] or [])
    nodes: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        tid: Optional[str] = None
        if i < len(lids) and lids[i] is not None:
            tid = str(lids[i])
        nodes.append(
            {
                "name": name,
                "taxon_id": tid,
                "depth": i,
            }
        )
    if not names:
        return {
            "taxon_id": str(taxon_id),
            "canonical_name": row["canonical_name"],
            "kingdom": row["kingdom"],
            "nodes": [],
            "message": "No lineage materialized; run backfill_kingdom_lineage after ETL creates parent links.",
        }
    return {
        "taxon_id": str(taxon_id),
        "canonical_name": row["canonical_name"],
        "kingdom": row["kingdom"],
        "nodes": nodes,
    }
