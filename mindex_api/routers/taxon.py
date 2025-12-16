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
    q_pattern = f"%{q}%" if q else None
    filters = {
        "q_pattern": q_pattern,
        "rank": rank,
        "limit": pagination.limit,
        "offset": pagination.offset,
    }

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
            updated_at
        FROM core.taxon
        WHERE (:q_pattern IS NULL OR canonical_name ILIKE :q_pattern OR common_name ILIKE :q_pattern)
          AND (:rank IS NULL OR rank = :rank)
        ORDER BY canonical_name
        LIMIT :limit OFFSET :offset
        """
    )
    count_stmt = text(
        """
        SELECT count(*) FROM core.taxon
        WHERE (:q_pattern IS NULL OR canonical_name ILIKE :q_pattern OR common_name ILIKE :q_pattern)
          AND (:rank IS NULL OR rank = :rank)
        """
    )

    result = await db.execute(stmt, filters)
    count_result = await db.execute(count_stmt, filters)
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
