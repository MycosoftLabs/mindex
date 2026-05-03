from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, pagination_params, require_api_key, PaginationParams
from ..contracts.v1.taxon import TaxonListResponse, TaxonResponse

router = APIRouter(
    prefix="/taxa",
    tags=["taxa"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=TaxonListResponse)
async def list_taxa(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    ids: Optional[str] = Query(None, description="Comma-separated taxon UUIDs for batch lookup (e.g., ?ids=uuid1,uuid2)."),
    q: Optional[str] = Query(None, description="Free-text search across canonical/common names."),
    rank: Optional[str] = Query(None, description="Exact rank filter."),
    source: Optional[str] = Query(None, description="Exact source filter (e.g., inat, gbif, mycobank)."),
    prefix: Optional[str] = Query(None, description="Prefix match on canonical_name (e.g., 'A' for A*)."),
    kingdom: Optional[str] = Query(
        None,
        description="Filter by high-level kingdom (Fungi, Plantae, Animalia, ...). Omit for all kingdoms.",
    ),
    lineage_contains: Optional[str] = Query(
        None,
        description="Match if any name in the materialized lineage array contains this substring (case-insensitive).",
    ),
    order_by: str = Query(
        "canonical_name",
        description="Sort field. Allowed: canonical_name, observations_count.",
    ),
    order: str = Query("asc", description="Sort order. Allowed: asc, desc."),
) -> TaxonListResponse:
    # Build dynamic WHERE clause to avoid asyncpg NULL parameter issues
    where_clauses = []
    params: dict = {
        "limit": pagination.limit,
        "offset": pagination.offset,
    }

    if q:
        q_pattern = f"%{q}%"
        where_clauses.append("(canonical_name ILIKE :q_pattern OR common_name ILIKE :q_pattern)")
        params["q_pattern"] = q_pattern
    if rank:
        where_clauses.append("rank = :rank")
        params["rank"] = rank
    if source:
        where_clauses.append("source = :source")
        params["source"] = source
    if prefix:
        prefix_pattern = f"{prefix}%"
        where_clauses.append("canonical_name ILIKE :prefix_pattern")
        params["prefix_pattern"] = prefix_pattern
    if ids:
        id_list = [x.strip() for x in ids.split(",") if x.strip()]
        if id_list:
            where_clauses.append("id = ANY(CAST(STRING_TO_ARRAY(:ids_csv, ',') AS uuid[]))")
            params["ids_csv"] = ",".join(id_list)
    if kingdom and kingdom.strip().lower() not in ("all", "any", ""):
        where_clauses.append("kingdom = :kingdom")
        params["kingdom"] = kingdom.strip()
    if lineage_contains and lineage_contains.strip():
        where_clauses.append(
            "EXISTS (SELECT 1 FROM unnest(COALESCE(lineage, ARRAY[]::text[])) x "
            "WHERE x ILIKE :lcp)"
        )
        params["lcp"] = f"%{lineage_contains.strip()}%"

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    order_by_normalized = (order_by or "").strip().lower()
    order_normalized = (order or "").strip().lower()

    if order_by_normalized not in {"canonical_name", "observations_count", "obs_count"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid order_by. Allowed: canonical_name, observations_count, obs_count.",
        )
    if order_normalized not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid order. Allowed: asc, desc.",
        )

    if order_by_normalized == "canonical_name":
        order_expr = "canonical_name"
    else:
        # Prefer bio.taxon_full.obs_count; fall back to metadata for legacy rows.
        order_expr = (
            "COALESCE(obs_count, "
            "CASE "
            "WHEN (metadata->>'observations_count') ~ '^[0-9]+$' "
            "THEN (metadata->>'observations_count')::int "
            "ELSE 0 END)"
        )

    stmt = text(
        f"""
        SELECT
            id,
            canonical_name,
            rank,
            common_name,
            author,
            description,
            source,
            metadata,
            kingdom,
            lineage,
            lineage_ids,
            external_ids,
            created_at,
            updated_at,
            obs_count,
            image_count,
            video_count,
            audio_count,
            genome_count,
            compound_link_count,
            interaction_count,
            publication_count,
            characteristic_count
        FROM bio.taxon_full
        WHERE {where_sql}
        ORDER BY {order_expr} {order_normalized}, canonical_name ASC
        LIMIT :limit OFFSET :offset
        """
    )
    count_stmt = text(
        f"""
        SELECT count(*) FROM bio.taxon_full
        WHERE {where_sql}
        """
    )

    result = await db.execute(stmt, params)
    count_result = await db.execute(count_stmt, params)
    total = count_result.scalar_one()

    rows = [dict(row) for row in result.mappings().all()]
    return TaxonListResponse(
        data=rows,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        },
    )


def _queue_incomplete_taxon(taxon_id: str, data: dict[str, Any]) -> None:
    """Append taxon to viewed-incomplete queue if missing image or description."""
    metadata = data.get("metadata") or {}
    default_photo = metadata.get("default_photo") or {}
    photo_url = default_photo.get("url") if isinstance(default_photo, dict) else None
    has_image = bool(photo_url and str(photo_url).strip())
    desc = data.get("description") or ""
    has_description = bool(desc and str(desc).strip())
    if has_image and has_description:
        return
    missing = []
    if not has_image:
        missing.append("image")
    if not has_description:
        missing.append("description")
    from ..utils.enrichment_queue import append_viewed_incomplete

    append_viewed_incomplete(str(taxon_id), data.get("canonical_name", "unknown"), missing=missing)


@router.get("/{taxon_id}", response_model=TaxonResponse)
async def get_taxon(
    taxon_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
    _api_key: Optional[str] = Depends(require_api_key),
) -> TaxonResponse:
    stmt = text(
        """
        SELECT
            t.id,
            t.canonical_name,
            t.rank,
            t.common_name,
            t.author,
            t.description,
            t.source,
            t.metadata,
            t.kingdom,
            t.lineage,
            t.lineage_ids,
            t.external_ids,
            t.created_at,
            t.updated_at,
            COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'id', tr.id,
                        'trait_name', tr.trait_name,
                        'value_text', tr.value_text,
                        'value_numeric', tr.value_numeric,
                        'value_unit', tr.value_unit,
                        'source', tr.source
                    )
                ) FILTER (WHERE tr.id IS NOT NULL),
                '[]'::jsonb
            ) AS traits
        FROM core.taxon t
        LEFT JOIN bio.taxon_trait tr ON tr.taxon_id = t.id
        WHERE t.id = :taxon_id
        GROUP BY t.id
        """
    )
    result = await db.execute(stmt, {"taxon_id": str(taxon_id)})
    row = result.mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Taxon not found")
    data = dict(row)
    data["traits"] = data.get("traits") or []
    # Queue incomplete taxa for ancestry_sync to prioritize enrichment
    if data.get("rank") == "species":
        background_tasks.add_task(_queue_incomplete_taxon, str(taxon_id), data)
    return TaxonResponse(**data)
