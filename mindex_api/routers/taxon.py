from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    q: Optional[str] = Query(None, description="Free-text search across canonical/common names."),
    rank: Optional[str] = Query(None, description="Exact rank filter."),
    source: Optional[str] = Query(None, description="Exact source filter (e.g., inat, gbif, mycobank)."),
    prefix: Optional[str] = Query(None, description="Prefix match on canonical_name (e.g., 'A' for A*)."),
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

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    order_by_normalized = (order_by or "").strip().lower()
    order_normalized = (order or "").strip().lower()

    if order_by_normalized not in {"canonical_name", "observations_count"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid order_by. Allowed: canonical_name, observations_count.",
        )
    if order_normalized not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid order. Allowed: asc, desc.",
        )

    if order_by_normalized == "canonical_name":
        order_expr = "canonical_name"
    else:
        # observations_count is stored under metadata->>'observations_count' as a string.
        # Guard casts to avoid non-numeric values throwing.
        order_expr = (
            "CASE "
            "WHEN (metadata->>'observations_count') ~ '^[0-9]+$' "
            "THEN (metadata->>'observations_count')::int "
            "ELSE 0 "
            "END"
        )

    stmt = text(
        f"""
        SELECT
            id,
            canonical_name,
            rank,
            common_name,
            authority,
            description,
            source,
            metadata,
            created_at,
            updated_at
        FROM core.taxon
        WHERE {where_sql}
        ORDER BY {order_expr} {order_normalized}, canonical_name ASC
        LIMIT :limit OFFSET :offset
        """
    )
    count_stmt = text(
        f"""
        SELECT count(*) FROM core.taxon
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


@router.get("/{taxon_id}", response_model=TaxonResponse)
async def get_taxon(
    taxon_id: UUID,
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
            t.authority,
            t.description,
            t.source,
            t.metadata,
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
    return TaxonResponse(**data)
