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

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

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
        ORDER BY canonical_name
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
            id,
            canonical_name,
            rank,
            common_name,
            authority,
            description,
            source,
            metadata,
            created_at,
            updated_at,
            COALESCE(traits, '[]'::jsonb) AS traits
        FROM app.v_taxon_with_traits
        WHERE id = :taxon_id
        """
    )
    result = await db.execute(stmt, {"taxon_id": str(taxon_id)})
    row = result.mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Taxon not found")
    data = dict(row)
    data["traits"] = data.get("traits") or []
    return TaxonResponse(**data)
