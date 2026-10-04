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

    def same_identity_name(left: Any, right: Any) -> bool:
        return " ".join(str(left or "").split()).casefold() == " ".join(str(right or "").split()).casefold()

    names = [str(name) for name in (row["lineage"] or [])]
    raw_ids = list(row["lineage_ids"] or [])
    issues: list[dict[str, Any]] = []
    ids_by_index: dict[int, UUID] = {}
    if not names:
        issues.append({"reason": "lineage_unavailable"})

    # lineage_ids is documented as parallel to lineage, but a malformed array
    # must never shift UUIDs onto unrelated names. Keep all names while dropping
    # every positional link when the arrays disagree in length.
    aligned = len(names) == len(raw_ids)
    if not aligned:
        issues.append({
            "reason": "lineage_arrays_misaligned",
            "name_count": len(names),
            "lineage_id_count": len(raw_ids),
        })
    else:
        for index, raw_id in enumerate(raw_ids):
            if raw_id is None:
                continue
            try:
                ids_by_index[index] = UUID(str(raw_id))
            except (ValueError, TypeError, AttributeError):
                issues.append({"index": index, "reason": "invalid_lineage_uuid"})

    # lineage is inclusive of the selected row in some stored records. Its
    # exact terminal name/UUID pair is already verified by the selected-row
    # lookup above; do not misclassify that self-link as an ancestor conflict.
    inclusive_selected_tip = bool(
        aligned
        and names
        and same_identity_name(names[-1], row["canonical_name"])
        and ids_by_index.get(len(names) - 1) == taxon_id
    )

    candidates = sorted(set(ids_by_index.values()), key=str)
    identity_by_id: dict[UUID, Any] = {}
    if candidates:
        identities = await db.execute(
            text("""
                SELECT id, kingdom, canonical_name, rank
                FROM core.taxon
                WHERE id = ANY(CAST(:ids AS uuid[]))
            """),
            {"ids": candidates},
        )
        identity_by_id = {UUID(str(item["id"])): item for item in identities.mappings().all()}

    def same_kingdom(left: Any, right: Any) -> bool:
        return not left or not right or str(left).strip().casefold() == str(right).strip().casefold()

    ancestors: list[dict[str, Any]] = []
    ancestor_count = len(names) - 1 if inclusive_selected_tip else len(names)
    for index, name in enumerate(names[:ancestor_count]):
        candidate_id = ids_by_index.get(index) if aligned else None
        candidate = identity_by_id.get(candidate_id) if candidate_id else None
        valid = bool(
            candidate
            and same_identity_name(candidate["canonical_name"], name)
            and same_kingdom(candidate["kingdom"], row["kingdom"])
            and candidate_id != taxon_id
        )
        if candidate_id and not valid:
            issues.append({"index": index, "reason": "lineage_uuid_identity_mismatch"})
        elif not candidate_id:
            issues.append({"index": index, "reason": "lineage_identity_unavailable"})
        if valid:
            node_id = str(candidate_id)
            rank = candidate["rank"] or "unknown"
            identity_status = "verified"
            rank_source = "core.taxon"
        else:
            node_id = f"name:{name}"
            rank = "unknown"
            identity_status = "name_only"
            rank_source = "unavailable"
        ancestors.append({
            "id": node_id,
            "taxon_id": str(candidate_id) if valid else None,
            "name": name,
            "rank": rank,
            "identity_status": identity_status,
            "rank_source": rank_source,
            "children": [],
        })

    # The exact selected row is authoritative for the selected tip. If an
    # inclusive lineage ends in the selected taxon, replace that position with
    # this separately verified row instead of duplicating it.
    if (
        not inclusive_selected_tip
        and ancestors
        and same_identity_name(ancestors[-1]["name"], row["canonical_name"])
    ):
        ancestors.pop()
    selected = {
        "id": str(taxon_id),
        "taxon_id": str(taxon_id),
        "name": row["canonical_name"],
        "rank": row["rank"] or "unknown",
        "identity_status": "verified_selected_row",
        "rank_source": "core.taxon",
        "children": [],
    }
    path = [*ancestors, selected]
    for parent, child in zip(path, path[1:]):
        parent["children"] = [child]
    tree = path[0] if path else None

    return {
        "success": True,
        "taxon_id": str(taxon_id),
        "canonical_name": row["canonical_name"],
        "kingdom": row["kingdom"],
        "status": "available" if not issues and names else "partial",
        "tree": tree,
        "lineage_provenance": {
            "source": "core.taxon.lineage",
            "identity_source": "core.taxon exact UUID lookup",
            "alignment": "parallel" if aligned else "misaligned",
            "status": "available" if not issues and names else "partial",
            "raw_lineage": names,
            "raw_lineage_ids": [str(value) if value is not None else None for value in raw_ids],
            "issues": issues,
        },
    }
