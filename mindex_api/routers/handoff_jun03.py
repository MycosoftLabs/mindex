"""
Handoff Jun 03, 2026 — bio summary, data catalog/search, library, storage aliases.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..dependencies import get_db_session

router = APIRouter(tags=["MINDEX Handoff Jun03"])


async def _scalar(db: AsyncSession, sql: str) -> int:
    try:
        return int((await db.execute(text(sql))).scalar() or 0)
    except Exception:
        await db.rollback()
        return 0


@router.get("/bio/summary")
async def bio_summary(db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """All-life biology plane counts for Encyclopedia / Overview."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "taxon": await _scalar(db, "SELECT COUNT(*) FROM core.taxon"),
        "observation": await _scalar(db, "SELECT COUNT(*) FROM obs.observation"),
        "observations_with_taxon_id": await _scalar(
            db, "SELECT COUNT(*) FROM obs.observation WHERE taxon_id IS NOT NULL"
        ),
        "taxon_external_id": await _scalar(
            db, "SELECT COUNT(*) FROM core.taxon_external_id"
        ),
        "genome": await _scalar(db, "SELECT COUNT(*) FROM bio.genome"),
        "genetic_sequence": await _scalar(
            db, "SELECT COUNT(*) FROM bio.genetic_sequence"
        ),
        "taxon_compound": await _scalar(
            db, "SELECT COUNT(*) FROM bio.taxon_compound"
        ),
        "compound": await _scalar(db, "SELECT COUNT(*) FROM bio.compound"),
        "taxa_with_images": await _scalar(
            db,
            "SELECT COUNT(*) FROM core.taxon "
            "WHERE metadata->>'default_photo' IS NOT NULL "
            "OR metadata->>'image_url' IS NOT NULL",
        ),
    }


@router.get("/data/catalog")
async def data_catalog(db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """Registered datasets and row counts (Data tab)."""
    from mindex_etl.jobs.run_all import create_job_registry
    from mindex_etl.scheduler import ETLScheduler

    registry = create_job_registry()
    scheduler = ETLScheduler()
    sources = []
    for name, job in sorted(registry.items(), key=lambda x: x[1].priority):
        sources.append(
            {
                "id": name,
                "source": job.source,
                "description": job.description,
                "interval_hours": scheduler.schedule.get(name, 24),
            }
        )

    counts = {
        "taxon": await _scalar(db, "SELECT COUNT(*) FROM core.taxon"),
        "observation": await _scalar(db, "SELECT COUNT(*) FROM obs.observation"),
        "genome": await _scalar(db, "SELECT COUNT(*) FROM bio.genome"),
        "genetic_sequence": await _scalar(
            db, "SELECT COUNT(*) FROM bio.genetic_sequence"
        ),
        "compound": await _scalar(db, "SELECT COUNT(*) FROM bio.compound"),
        "taxon_compound": await _scalar(
            db, "SELECT COUNT(*) FROM bio.taxon_compound"
        ),
    }
    return {"sources": sources, "table_counts": counts, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/data/search")
async def data_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(25, ge=1, le=100),
    domains: Optional[str] = Query(None, description="Comma-separated domain filter"),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Thin wrapper: taxa + compounds text search (unified search is separate route)."""
    pattern = f"%{q.strip()}%"
    taxa_rows = (
        await db.execute(
            text(
                """
                SELECT id::text, canonical_name, rank, kingdom, common_name, source
                FROM core.taxon
                WHERE canonical_name ILIKE :p OR common_name ILIKE :p
                ORDER BY canonical_name
                LIMIT :lim
                """
            ),
            {"p": pattern, "lim": limit},
        )
    ).mappings().all()

    compound_rows: list[Any] = []
    try:
        compound_rows = (
            await db.execute(
                text(
                    """
                    SELECT id::text, name, compound_type, chemical_class
                    FROM bio.compound
                    WHERE name ILIKE :p
                    ORDER BY name
                    LIMIT :lim
                    """
                ),
                {"p": pattern, "lim": limit},
            )
        ).mappings().all()
    except Exception:
        await db.rollback()

    return {
        "query": q,
        "taxa": [dict(r) for r in taxa_rows],
        "compounds": [dict(r) for r in compound_rows],
        "domains": domains.split(",") if domains else ["taxa", "compounds"],
    }


def _library_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("MINDEX_LIBRARY_ROOT", "MINDEX_NAS_LIBRARY", "NAS_LIBRARY_PATH"):
        raw = os.environ.get(key, "").strip()
        if raw:
            roots.append(Path(raw))
    default = Path("/mnt/nas/mindex/library")
    if default not in roots:
        roots.append(default)
    return roots


@router.get("/library/catalog")
async def library_catalog(
    limit: int = Query(100, ge=1, le=500),
    path: Optional[str] = Query(None, description="Subpath under library root"),
) -> dict[str, Any]:
    """NAS library listing (Request 012) — read-only directory scan."""
    items: list[dict[str, Any]] = []
    scanned: list[str] = []
    for root in _library_roots():
        if not root.is_dir():
            continue
        scanned.append(str(root))
        target = root / path if path else root
        if not target.is_dir():
            continue
        try:
            for entry in sorted(target.iterdir())[:limit]:
                if entry.name.startswith("."):
                    continue
                st = entry.stat()
                items.append(
                    {
                        "name": entry.name,
                        "path": str(entry.relative_to(root)).replace("\\", "/"),
                        "is_dir": entry.is_dir(),
                        "size_bytes": st.st_size if entry.is_file() else None,
                        "modified_at": datetime.fromtimestamp(
                            st.st_mtime, tz=timezone.utc
                        ).isoformat(),
                        "root": str(root),
                    }
                )
        except OSError:
            continue
        break

    return {
        "items": items,
        "roots_scanned": scanned,
        "mount_available": bool(items or scanned),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/storage/nodes")
async def storage_nodes_alias(db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """Alias for network.storage_node (website Storage tab)."""
    try:
        result = await db.execute(
            text(
                """
                SELECT id::text, kind, label, host, region, capacity_bytes, used_bytes,
                       owner, last_seen_at, created_at, metadata
                FROM network.storage_node
                ORDER BY created_at DESC
                LIMIT 200
                """
            )
        )
        return {"items": [dict(r) for r in result.mappings().all()]}
    except Exception:
        await db.rollback()
        return {
            "items": [
                {
                    "id": "nas-primary",
                    "kind": "nas",
                    "label": "MINDEX NAS",
                    "host": "192.168.0.105",
                    "region": "lab",
                    "note": "network.storage_node table not migrated; fallback node",
                }
            ],
            "fallback": True,
        }


@router.get("/storage/sync/status")
async def storage_sync_status(db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """ETL + NAS sync visibility for Storage tab."""
    from ..storage import get_storage

    storage = get_storage()
    nas = storage.nas_status() if hasattr(storage, "nas_status") else {}
    etl_running = False
    try:
        import subprocess

        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        etl_running = "etl" in (result.stdout or "").lower()
    except Exception:
        pass
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nas": nas,
        "etl_container_running": etl_running,
        "observation_count": await _scalar(db, "SELECT COUNT(*) FROM obs.observation"),
    }
