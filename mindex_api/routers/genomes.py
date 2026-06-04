"""
Genome assemblies — read-only from bio.genome joined to core.taxon.
No mock payloads: empty list when the table is empty or unavailable.
"""
from __future__ import annotations

from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

router = APIRouter(tags=["Genomes"])


async def _genome_table_ready(db: AsyncSession) -> bool:
    try:
        r = await db.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'bio' AND table_name = 'genome'"
                ")"
            )
        )
        return bool(r.scalar())
    except Exception:
        await db.rollback()
        return False


@router.get("/genomes")
async def get_genomes(
    session: AsyncSession = Depends(get_db_session),
    species: Optional[str] = Query(
        None,
        description="Partial match on taxon scientific name (canonical_name).",
    ),
    taxon_id: Optional[UUID] = Query(None, description="Filter by taxon id."),
    kingdom: Optional[str] = Query(
        None,
        description="Filter by kingdom (Fungi, Plantae, ...). Omit for all.",
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    if not await _genome_table_ready(session):
        return {
            "genomes": [],
            "pagination": {"limit": limit, "offset": offset, "total": 0},
            "message": "bio.genome table missing; run genetics/fungidb ETL",
        }

    where_parts: List[str] = ["TRUE"]
    params: dict = {"limit": limit, "offset": offset}
    if species and species.strip():
        where_parts.append("t.canonical_name ILIKE :sp")
        params["sp"] = f"%{species.strip()}%"
    if taxon_id:
        where_parts.append("g.taxon_id = :taxon_id")
        params["taxon_id"] = str(taxon_id)
    if kingdom and kingdom.strip().lower() not in ("all", "any", ""):
        where_parts.append("t.kingdom = :kingdom")
        params["kingdom"] = kingdom.strip()

    wh = " AND ".join(where_parts)
    try:
        count_sql = text(
            f"SELECT COUNT(*) FROM bio.genome g "
            f"JOIN core.taxon t ON t.id = g.taxon_id WHERE {wh}"
        )
        total = int((await session.execute(count_sql, params)).scalar() or 0)
    except Exception:
        await session.rollback()
        return {
            "genomes": [],
            "pagination": {"limit": limit, "offset": offset, "total": 0},
            "message": "bio.genome query unavailable",
        }

    data_sql = text(
        f"""
        SELECT
            g.id,
            g.taxon_id,
            g.source,
            g.accession,
            g.assembly_level,
            g.release_date,
            g.metadata,
            g.created_at,
            t.kingdom,
            t.canonical_name AS scientific_name,
            t.common_name
        FROM bio.genome g
        JOIN core.taxon t ON t.id = g.taxon_id
        WHERE {wh}
        ORDER BY g.release_date DESC NULLS LAST, g.accession
        LIMIT :limit OFFSET :offset
        """
    )
    try:
        res = await session.execute(data_sql, params)
    except Exception:
        await session.rollback()
        return {
            "genomes": [],
            "pagination": {"limit": limit, "offset": offset, "total": 0},
            "message": "bio.genome query unavailable",
        }
    rows: List[dict[str, Any]] = []
    for row in res.mappings().all():
        r = dict(row)
        r["id"] = str(r["id"])
        r["taxon_id"] = str(r["taxon_id"])
        rows.append(r)

    return {
        "genomes": rows,
        "pagination": {"limit": limit, "offset": offset, "total": int(total)},
    }
