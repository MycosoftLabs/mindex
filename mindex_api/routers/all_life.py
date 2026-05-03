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
    r = await db.execute(
        text(
            """
            SELECT id, source_taxon_id, target_taxon_id, interaction_type::text, evidence_source,
                   evidence_url, ST_AsGeoJSON(location)::json AS location, metadata, created_at
            FROM bio.taxon_interaction
            WHERE source_taxon_id = :id OR target_taxon_id = :id
            ORDER BY created_at DESC
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
    total = c.scalar_one()
    return {"data": rows, "pagination": {"limit": limit, "offset": offset, "total": total}}


@router.get("/taxa/{taxon_id}/media")
async def list_media(
    taxon_id: UUID,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    v = await db.execute(
        text("SELECT * FROM media.video WHERE taxon_id = :id ORDER BY created_at DESC"),
        {"id": str(taxon_id)},
    )
    a = await db.execute(
        text("SELECT * FROM media.audio WHERE taxon_id = :id ORDER BY created_at DESC"),
        {"id": str(taxon_id)},
    )
    return {
        "video": [dict(x) for x in v.mappings().all()],
        "audio": [dict(x) for x in a.mappings().all()],
    }


@router.get("/taxa/{taxon_id}/publications")
async def list_publications(
    taxon_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(200, le=2000, ge=1),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    r = await db.execute(
        text(
            """
            SELECT p.*, pt.relevance_score, pt.created_at AS linked_at
            FROM bio.publication_taxon pt
            JOIN core.publications p ON p.id = pt.publication_id
            WHERE pt.taxon_id = :id
            ORDER BY pt.relevance_score DESC NULLS LAST, pt.created_at DESC
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
    total = c.scalar_one()
    return {"data": rows, "pagination": {"limit": limit, "offset": offset, "total": total}}


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
