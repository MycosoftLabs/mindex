"""
Licensed fungi-only export for MycoDAO Blocks.

Hard-enforces kingdom=Fungi on the server. Rejects kingdom overrides and
nonfungal ID lookups through this identity. Bind callers to a purpose-scoped
credential (MINDEX_MYCODAO_FUNGI_EXPORT_KEYS) — not treasury/signing authority.

Snapshot semantics: each export session returns a stable snapshot_id bound to
exact filters, ordering, page size, and a high-water `updated_at` watermark
observed at session open. Pages must stay inside the same snapshot.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/integrations/mycodao/fungi",
    tags=["integrations-mycodao-fungi"],
)

# In-process snapshot registry (single-host API). Durable stores should
# persist checkpoints on the MycoDAO importer side; this only binds page
# membership for one export session.
_SNAPSHOTS: dict[str, dict[str, Any]] = {}

_SPECIES_RANKS = ("species", "sp.", "sp")
_FUNGI_KINGDOM = "Fungi"


def _export_keys() -> list[str]:
    raw = (
        os.environ.get("MINDEX_MYCODAO_FUNGI_EXPORT_KEYS", "")
        or os.environ.get("MINDEX_MYCODAO_FUNGI_EXPORT_KEY", "")
    )
    return [t.strip() for t in raw.split(",") if t.strip()]


async def require_mycodao_fungi_export_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_mycodao_export_key: Optional[str] = Header(None, alias="X-Mycodao-Fungi-Export-Key"),
) -> str:
    """Purpose-scoped read export identity. Fail closed when unset in production."""
    keys = _export_keys()
    presented = (x_mycodao_export_key or x_api_key or "").strip()
    env = (os.environ.get("MINDEX_ENV") or os.environ.get("ENVIRONMENT") or "").strip().lower()
    is_prod = env in ("production", "prod")

    if not keys:
        if is_prod:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MycoDAO fungi export keys not configured",
            )
        logger.warning("MINDEX_MYCODAO_FUNGI_EXPORT_KEYS unset — open in non-production only")
        return presented or "dev-open"

    if not presented or presented not in keys:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return presented


def _normalize_rank(raw: Optional[str]) -> tuple[str, str]:
    value = (raw or "").strip()
    lower = value.lower()
    if lower in {"species", "sp.", "sp"}:
        return "species", value or "species"
    return lower or "unknown", value or "unknown"


def _effective_kingdom(row: dict[str, Any]) -> tuple[Optional[str], str]:
    """Return (effective_kingdom, provenance). Never guess unclassified → Fungi."""
    raw = row.get("kingdom")
    if isinstance(raw, str) and raw.strip():
        k = raw.strip()
        if k.lower() == "fungi":
            return "Fungi", "core.taxon.kingdom"
        return k, "core.taxon.kingdom"

    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for key in ("validated_kingdom", "effective_kingdom", "kingdom"):
        v = metadata.get(key)
        if isinstance(v, str) and v.strip().lower() == "fungi":
            return "Fungi", f"metadata.{key}"
    return None, "unclassified"


def _row_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _allowlisted_dto(row: dict[str, Any], *, effective_kingdom: str, kingdom_provenance: str) -> dict[str, Any]:
    norm_rank, raw_rank = _normalize_rank(row.get("rank"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    photo = metadata.get("default_photo") if isinstance(metadata.get("default_photo"), dict) else {}
    external_ids = row.get("external_ids") if isinstance(row.get("external_ids"), dict) else {}
    lineage = row.get("lineage") if isinstance(row.get("lineage"), list) else []

    dto = {
        "mindexTaxonUuid": str(row["id"]),
        "canonicalName": row.get("canonical_name") or "",
        "commonName": row.get("common_name") or "",
        "rank": norm_rank,
        "rankRaw": raw_rank,
        "kingdom": effective_kingdom,
        "kingdomProvenance": kingdom_provenance,
        "lineage": lineage,
        "externalIds": external_ids,
        "description": row.get("description"),
        "source": row.get("source"),
        "author": row.get("author"),
        "createdAt": row.get("created_at").isoformat() if row.get("created_at") else None,
        "updatedAt": row.get("updated_at").isoformat() if row.get("updated_at") else None,
        "obsCount": int(row.get("obs_count") or 0),
        "imageCount": int(row.get("image_count") or 0),
        "referenceImage": None,
        "tokenEligibleHint": False,
        "dataQuality": {
            "hasExternalIds": bool(external_ids),
            "hasDescription": bool((row.get("description") or "").strip()),
            "hasImage": False,
        },
    }

    url = photo.get("url") or photo.get("medium_url") or photo.get("original_url")
    license_text = photo.get("license") or photo.get("license_code") or photo.get("attribution")
    attribution = photo.get("attribution") or photo.get("attribution_name")
    if url and license_text:
        dto["referenceImage"] = {
            "url": str(url),
            "license": str(license_text),
            "attribution": str(attribution) if attribution else None,
            "source": "mindex.metadata.default_photo",
        }
        dto["dataQuality"]["hasImage"] = True

    # Token eligibility is a separate gate; hint only when identity is complete.
    dto["tokenEligibleHint"] = (
        norm_rank == "species"
        and bool(external_ids)
        and effective_kingdom == _FUNGI_KINGDOM
    )
    dto["sourceRecordHash"] = _row_hash(dto)
    return dto


_FROM_CLAUSE = """(
    SELECT
        t.id,
        t.canonical_name,
        t.rank,
        t.common_name,
        COALESCE(t.author, t.authority) AS author,
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
            (SELECT COUNT(*)::bigint FROM obs.observation o WHERE o.taxon_id = t.id),
            0
        ) AS obs_count,
        COALESCE(
            (SELECT COUNT(*)::bigint FROM media.image i WHERE i.taxon_id = t.id),
            0
        ) AS image_count
    FROM core.taxon t
) AS taxon_export"""


async def _open_or_get_snapshot(
    db: AsyncSession,
    *,
    snapshot_id: Optional[str],
    license_grant_id: str,
    page_size: int,
    include_non_species: bool,
) -> dict[str, Any]:
    if snapshot_id:
        snap = _SNAPSHOTS.get(snapshot_id)
        if not snap:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unknown or expired export snapshot",
            )
        if snap["licenseGrantId"] != license_grant_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Snapshot/grant substitution rejected",
            )
        return snap

    # High-water mark for this session (not a DB txn snapshot, but binds pages).
    hw = await db.execute(
        text(
            """
            SELECT MAX(updated_at) AS hw, COUNT(*)::bigint AS total
            FROM core.taxon
            WHERE UPPER(COALESCE(kingdom, '')) = 'FUNGI'
              AND (
                :include_non_species
                OR LOWER(COALESCE(rank, '')) IN ('species', 'sp.', 'sp')
              )
            """
        ),
        {"include_non_species": include_non_species},
    )
    row = hw.mappings().one()
    sid = str(uuid4())
    snap = {
        "snapshotId": sid,
        "licenseGrantId": license_grant_id,
        "openedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pageSize": page_size,
        "includeNonSpecies": include_non_species,
        "highWaterUpdatedAt": row["hw"].isoformat() if row["hw"] else None,
        "expectedTotal": int(row["total"] or 0),
        "filters": {
            "kingdom": _FUNGI_KINGDOM,
            "ranks": list(_SPECIES_RANKS) if not include_non_species else "all_fungal_ranks",
            "orderBy": "canonical_name",
            "order": "asc",
        },
    }
    _SNAPSHOTS[sid] = snap
    # Cap registry size
    if len(_SNAPSHOTS) > 64:
        oldest = sorted(_SNAPSHOTS.items(), key=lambda kv: kv[1]["openedAt"])[:16]
        for k, _ in oldest:
            _SNAPSHOTS.pop(k, None)
    return snap


@router.get("/export")
async def export_fungi_page(
    db: AsyncSession = Depends(get_db_session),
    _key: str = Depends(require_mycodao_fungi_export_key),
    license_grant_id: str = Query(..., min_length=8, max_length=128),
    snapshot_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_non_species: bool = Query(
        False,
        description="When true, include fungal ranks beyond species for reference library.",
    ),
    q: Optional[str] = Query(None, max_length=200),
) -> dict[str, Any]:
    """Paginated fungi-only export. Kingdom cannot be overridden."""
    snap = await _open_or_get_snapshot(
        db,
        snapshot_id=snapshot_id,
        license_grant_id=license_grant_id,
        page_size=limit,
        include_non_species=include_non_species,
    )
    if snap["includeNonSpecies"] != include_non_species:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Snapshot filter substitution rejected",
        )

    where = [
        "UPPER(COALESCE(kingdom, '')) = 'FUNGI'",
    ]
    params: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
    }
    if not include_non_species:
        where.append("LOWER(COALESCE(rank, '')) IN ('species', 'sp.', 'sp')")
    if snap.get("highWaterUpdatedAt"):
        where.append("(updated_at IS NULL OR updated_at <= CAST(:hw AS timestamptz))")
        params["hw"] = snap["highWaterUpdatedAt"]
    if q and q.strip():
        where.append("(canonical_name ILIKE :q OR common_name ILIKE :q)")
        params["q"] = f"%{q.strip()}%"

    where_sql = " AND ".join(where)
    try:
        result = await db.execute(
            text(
                f"""
                SELECT * FROM {_FROM_CLAUSE}
                WHERE {where_sql}
                ORDER BY canonical_name ASC, id ASC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        count_result = await db.execute(
            text(f"SELECT COUNT(*) FROM {_FROM_CLAUSE} WHERE {where_sql}"),
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        )
    except Exception as exc:
        await db.rollback()
        logger.exception("mycodao fungi export query failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Fungal export unavailable",
        ) from exc

    total = int(count_result.scalar_one() or 0)
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    nonfungal = 0

    for raw in result.mappings().all():
        row = dict(raw)
        eff, prov = _effective_kingdom(row)
        if eff != _FUNGI_KINGDOM:
            nonfungal += 1
            excluded.append(
                {
                    "mindexTaxonUuid": str(row.get("id")),
                    "reason": "kingdom_not_fungi_or_unclassified",
                    "kingdomProvenance": prov,
                    "rawKingdom": row.get("kingdom"),
                }
            )
            continue
        items.append(_allowlisted_dto(row, effective_kingdom=eff, kingdom_provenance=prov))

    next_offset = offset + limit if offset + limit < total else None
    return {
        "availability": "available" if items or total == 0 else "partial",
        "snapshot": {
            "snapshotId": snap["snapshotId"],
            "licenseGrantId": snap["licenseGrantId"],
            "openedAt": snap["openedAt"],
            "highWaterUpdatedAt": snap["highWaterUpdatedAt"],
            "expectedTotal": snap["expectedTotal"],
            "filters": snap["filters"],
        },
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "nextOffset": next_offset,
            "complete": next_offset is None,
        },
        "data": items,
        "excluded": excluded,
        "accounting": {
            "pageAccepted": len(items),
            "pageExcluded": len(excluded),
            "pageNonfungalRejected": nonfungal,
        },
        "rights": {
            "licensor": "MINDEX",
            "licensee": "MycoDAO",
            "scope": "fungi_reference_replica",
            "note": "Recording this grant locally is not executing a legal agreement.",
        },
    }


@router.get("/taxa/{taxon_id}")
async def get_fungi_taxon(
    taxon_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    _key: str = Depends(require_mycodao_fungi_export_key),
    license_grant_id: str = Query(..., min_length=8, max_length=128),
    read_only: bool = Query(True, description="Prefer read-only path; do not queue enrichment."),
) -> dict[str, Any]:
    """Single taxon detail — Fungi only. Nonfungal UUIDs fail closed."""
    del read_only  # enrichment intentionally not queued from this route
    try:
        result = await db.execute(
            text(
                f"""
                SELECT * FROM {_FROM_CLAUSE}
                WHERE id = CAST(:id AS uuid)
                LIMIT 1
                """
            ),
            {"id": str(taxon_id)},
        )
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Fungal detail unavailable",
        ) from exc

    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Taxon not found")

    eff, prov = _effective_kingdom(dict(row))
    if eff != _FUNGI_KINGDOM:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nonfungal taxa are outside the MycoDAO fungi export scope",
        )

    dto = _allowlisted_dto(dict(row), effective_kingdom=eff, kingdom_provenance=prov)
    return {
        "availability": "available",
        "licenseGrantId": license_grant_id,
        "data": dto,
    }
