from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, require_api_key

router = APIRouter(
    tags=["Phylogeny"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/phylogeny")
async def get_phylogeny(
    taxon_id: Optional[UUID] = Query(
        None,
        description="MINDEX taxon UUID — builds nested tree from materialized lineage when available.",
    ),
    clade: Optional[str] = Query(None, description="Deprecated: ignored; use taxon_id."),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Get a taxonomic tree. When taxon_id is set, use core.taxon lineage (no sample/mock data)."""
    _ = clade
    if not taxon_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide taxon_id (MINDEX UUID) to build a tree from materialized lineage.",
        )
    r = await db.execute(
        text("SELECT id, kingdom, canonical_name, rank, lineage, lineage_ids FROM core.taxon WHERE id = :id"),
        {"id": str(taxon_id)},
    )
    row = r.mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Taxon not found")
    names = list(row["lineage"] or [])
    if not names:
        return {
            "success": True,
            "taxon_id": str(taxon_id),
            "canonical_name": row["canonical_name"],
            "kingdom": row["kingdom"],
            "tree": None,
            "message": "No lineage for this taxon; run ETL and backfill_kingdom_lineage.",
        }
    # Nested path from root to tip (names only; links via lineage_ids when present)
    lid = list(row["lineage_ids"] or [])

    def node_at(i: int) -> dict[str, Any]:
        tid: Optional[str] = None
        if i < len(lid) and lid[i] is not None:
            tid = str(lid[i])
        return {
            "id": tid or f"name:{names[i]}",
            "name": names[i],
            "rank": "clade" if i < len(names) - 1 else (row["rank"] or "species"),
            "children": [],
        }

    root: Optional[dict[str, Any]] = None
    for i in range(len(names)):
        n = node_at(i)
        if root is None:
            root = n
        else:
            # attach as child chain (single path)
            cur = root
            while cur["children"]:
                cur = cur["children"][0]
            cur["children"] = [n]
    return {"success": True, "taxon_id": str(taxon_id), "canonical_name": row["canonical_name"], "kingdom": row["kingdom"], "tree": root}
