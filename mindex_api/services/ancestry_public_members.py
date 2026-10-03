"""Read-only FungiP enrichment for ordinary all-life taxon rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..contracts.v1.ancestry_index import FungiPIndexAvailability, FungiPIndexMember
from mindex_etl.fungip.first40_detail import public_first40_launch


_PUBLIC_MEMBER_SQL = text("""
    WITH indexed AS (
        SELECT s.species_id, s.taxon_id, s.accepted_name, s.record, s.external_ids,
               s.verified_its_sequence, s.image_valid, s.sequence_valid,
               s.missing_data_flags, s.validation_errors, s.record_sha256, s.catalog_sha256,
               s.resolution_status,
               matches.candidate_count, matches.candidate_taxon_id,
               t.canonical_name, t.common_name, t.metadata, t.kingdom AS canonical_kingdom,
               page.canonical_url AS page_canonical_url,
               page.record_sha256 AS page_record_sha256, page.evidence AS page_evidence,
               EXISTS (
                   SELECT 1 FROM fungip.token_attempt attempt
                   WHERE attempt.species_id = s.species_id
                     AND attempt.status = 'confirmed'
                     AND attempt.network = 'solana-mainnet-beta'
               ) AS token_confirmed
        FROM fungip.species s
        LEFT JOIN LATERAL (
            SELECT COUNT(DISTINCT external_id.taxon_id)::int AS candidate_count,
                   (ARRAY_AGG(DISTINCT external_id.taxon_id))[1] AS candidate_taxon_id
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(s.external_ids) = 'array' THEN s.external_ids ELSE '[]'::jsonb END
            ) AS source_identifier(value)
            JOIN core.taxon_external_id external_id
              ON external_id.source = source_identifier.value->>'source'
             AND external_id.external_id = source_identifier.value->>'external_id'
        ) matches ON TRUE
        JOIN core.taxon t ON t.id = s.taxon_id
        LEFT JOIN fungip.page_verification page
          ON page.species_id = s.species_id AND page.taxon_id = s.taxon_id
        WHERE s.taxon_id = ANY(:taxon_ids)
          AND s.resolution_status = 'resolved'
          AND matches.candidate_count = 1
          AND matches.candidate_taxon_id = s.taxon_id
          AND LOWER(t.rank) = 'species'
          AND LOWER(t.kingdom) = 'fungi'
          AND t.canonical_name = s.record->>'accepted_name'
          AND s.accepted_name = s.record->>'accepted_name'
    )
    SELECT * FROM indexed ORDER BY species_id
""")

_REQUIRED_PAGE_EVIDENCE = (
    "name_checked", "taxonomy_checked", "dna_checked", "download_checked", "attribution_checked",
)
_IDENTITY_CONFLICT_STATES = {"identity_conflict", "source_conflict", "duplicate_canonical"}


def _json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def merge_source_values(primary: Any, fallback: Any = None) -> list[str]:
    """Keep parent and launch source metadata, in order, without duplicates."""
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*_json_list(primary), *_json_list(fallback)]:
        normalized = str(value)
        if normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)
    return merged


def project_fungip_identity(row: dict[str, Any]) -> tuple[str, str, Optional[str]]:
    """Preserve importer resolution state and recheck its exact identifier invariants."""
    status = str(row.get("resolution_status") or "unresolved")
    candidate_count = int(row.get("candidate_count") or 0)
    candidate_id = row.get("candidate_taxon_id")
    stored_id = row.get("stored_taxon_id")
    if status in _IDENTITY_CONFLICT_STATES:
        return "ambiguous", status, None
    if candidate_count > 1:
        return "ambiguous", "multiple_external_id_matches", None
    if status != "resolved":
        return "unresolved", status, None
    if stored_id is None:
        return "ambiguous", "resolved_status_missing_stored_taxon_id", None
    if candidate_count == 0:
        return "unresolved", "resolved_crosswalk_no_longer_matches", None
    if str(stored_id) != str(candidate_id):
        return "ambiguous", "stored_taxon_conflicts_with_external_id", None
    if str(row.get("canonical_rank") or "").lower() != "species":
        return "ambiguous", "canonical_rank_mismatch", None
    if str(row.get("canonical_kingdom") or "").lower() != "fungi":
        return "ambiguous", "canonical_kingdom_mismatch", None
    record_name = _json_object(row.get("record")).get("accepted_name")
    if row.get("canonical_name") != record_name or row.get("accepted_name") != record_name:
        return "ambiguous", "canonical_name_mismatch", None
    if candidate_id is not None:
        return "linked", "unique_exact_external_id", str(candidate_id)
    return "unresolved", "no_unique_exact_external_id_match", None


def _utc_z(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


async def load_validated_first40_associations(
    db: AsyncSession,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Validate source launch rows against the current parent record before exposure."""
    result = await db.execute(text("""
        SELECT association.*,
               batch.catalog_sha256 AS batch_catalog_sha256,
               batch.launch_sha256 AS batch_launch_sha256,
               batch.handoff_sha256 AS batch_handoff_sha256,
               batch.correction_sha256 AS batch_correction_sha256,
               batch.corrected_input_version AS batch_corrected_input_version,
               batch.authority_approval_reference, batch.launch_schema, batch.superseded_encoding,
               batch.source_reported_as_of_utc, batch.source_reported_as_of_pt,
               batch.correction_recorded_at_utc,
               batch.verification_basis AS batch_verification_basis,
               batch.new_chain_verified AS batch_new_chain_verified,
               species.accepted_name AS parent_accepted_name,
               species.record AS parent_record,
               species.image_valid AS parent_image_valid,
               species.sequence_valid AS parent_sequence_valid
        FROM fungip.first40_launch_association association
        JOIN fungip.first40_source_batch batch
          ON batch.snapshot_sha256 = association.snapshot_sha256
        JOIN fungip.species species ON species.species_id = association.species_id
        ORDER BY association.species_id
    """))
    valid: dict[str, dict[str, Any]] = {}
    invalid: set[str] = set()
    for raw in result.mappings().all():
        row = dict(raw)
        species_id = str(row.get("species_id") or "")
        row["source_reported_as_of_utc"] = _utc_z(row.get("source_reported_as_of_utc"))
        row["correction_recorded_at_utc"] = _utc_z(row.get("correction_recorded_at_utc"))
        row["launched_at"] = _utc_z(row.get("launched_at"))
        parent = {
            "species_id": species_id,
            "accepted_name": row.get("parent_accepted_name"),
            "record": row.get("parent_record"),
            "image_valid": row.get("parent_image_valid"),
            "sequence_valid": row.get("parent_sequence_valid"),
        }
        try:
            public = public_first40_launch(row, parent)
        except (TypeError, ValueError):
            invalid.add(species_id)
        else:
            public.update({
                "snapshot_sha256": row.get("snapshot_sha256"),
                "payload_sha256": row.get("payload_sha256"),
                "catalog_sha256": row.get("batch_catalog_sha256"),
                "launch_sha256": row.get("batch_launch_sha256"),
                "handoff_sha256": row.get("batch_handoff_sha256"),
                "correction_sha256": row.get("batch_correction_sha256"),
                "launch_schema": row.get("launch_schema"),
                "superseded_encoding": row.get("superseded_encoding"),
                "corrected_input_version": row.get("batch_corrected_input_version"),
                "authority_approval_reference": row.get("authority_approval_reference"),
                "verification_basis": row.get("batch_verification_basis"),
                "new_chain_verified": row.get("batch_new_chain_verified"),
            })
            valid[species_id] = public
    return valid, invalid


def _public_member(row: dict[str, Any]) -> FungiPIndexMember:
    record = _json_object(row.get("record"))
    dna = _json_object(record.get("dna"))
    evidence = _json_object(row.get("page_evidence"))
    image_errors = any("image" in str(error).lower() for error in _json_list(row.get("validation_errors")))
    image = record.get("image") if row.get("image_valid") and not image_errors else None
    current_page = (
        row.get("page_record_sha256") == row.get("record_sha256")
        and all(evidence.get(key) is True for key in _REQUIRED_PAGE_EVIDENCE)
    )
    token_confirmed = bool(row.get("token_confirmed"))
    return FungiPIndexMember(
        species_id=str(row["species_id"]),
        mindex_uuid=UUID(str(row["taxon_id"])),
        canonical_taxon_uuid=UUID(str(row["taxon_id"])),
        accepted_name=str(row.get("accepted_name") or ""),
        common_name=row.get("common_name") or record.get("common_name"),
        requested_name=(str(record["requested_name"]) if record.get("requested_name") is not None else None),
        fungal_group=record.get("group"),
        synonyms=[str(value) for value in _json_list(record.get("synonyms"))],
        catalog_review_flags=[str(value) for value in _json_list(record.get("catalog_review_flags"))],
        ticker=record.get("ticker"),
        accession_version=dna.get("accession_version"),
        reference_dna_available=bool(row.get("sequence_valid")),
        complete_its_available=bool(row.get("sequence_valid") and row.get("verified_its_sequence")),
        token_confirmed=token_confirmed,
        feature_label=(
            "Confirmed token receipt; biological claims not chain-validated"
            if token_confirmed else "Research collection member"
        ),
        missing_data_flags=[str(value) for value in _json_list(row.get("missing_data_flags"))],
        image=image,
        resolution_status=str(row.get("resolution_status") or "resolved"),
        identity_state="linked",
        identity_reason="resolved_unique_exact_external_id",
        canonical_url=row.get("page_canonical_url") if current_page else None,
        record_sha256=str(row.get("record_sha256") or ""),
        catalog_sha256=str(row.get("catalog_sha256") or ""),
        validation_errors=_json_list(row.get("validation_errors")),
        source_identifiers=[item for item in _json_list(row.get("external_ids")) if isinstance(item, dict)],
        taxonomy=_json_object(record.get("taxonomy")),
        first40_launch=None,
        chain_receipt_status="confirmed" if token_confirmed else "not_confirmed",
    )


async def load_public_fungip_members(
    db: AsyncSession, taxon_ids: list[UUID],
) -> tuple[dict[str, FungiPIndexMember], FungiPIndexAvailability]:
    """Return exact resolved links without changing the all-life result set."""
    try:
        table = (await db.execute(text("SELECT to_regclass('fungip.species')"))).scalar_one_or_none()
        if table is None:
            return {}, FungiPIndexAvailability(status="unavailable", reason="source_table_missing")
        if not taxon_ids:
            return {}, FungiPIndexAvailability(status="available")
        result = await db.execute(_PUBLIC_MEMBER_SQL, {"taxon_ids": taxon_ids})
        members = {
            str(row["taxon_id"]): _public_member(dict(row))
            for row in result.mappings().all()
        }
        return members, FungiPIndexAvailability(status="available")
    except Exception:
        await db.rollback()
        return {}, FungiPIndexAvailability(status="error", reason="query_failed")


async def search_public_fungip(
    db: AsyncSession, query: str, limit: int, kingdom: Optional[str] = None,
) -> tuple[list[dict[str, Any]], FungiPIndexAvailability]:
    """Search only source rows and exact crosswalk links; never infer identity by name."""
    try:
        normalized_kingdom = (kingdom or "").strip().lower()
        if normalized_kingdom not in {"", "all", "any", "fungi"}:
            return [], FungiPIndexAvailability(status="available")
        tables = (await db.execute(text("""
            SELECT to_regclass('fungip.species') AS species_table,
                   to_regclass('fungip.first40_launch_association') AS launch_table,
                   to_regclass('fungip.first40_source_batch') AS batch_table
        """))).mappings().one()
        if tables["species_table"] is None:
            return [], FungiPIndexAvailability(status="unavailable", reason="source_table_missing")

        valid_launches: dict[str, dict[str, Any]] = {}
        if tables["launch_table"] is not None and tables["batch_table"] is not None:
            valid_launches, _ = await load_validated_first40_associations(db)
        needle = query.casefold()
        launch_ids = [
            species_id for species_id, launch in valid_launches.items()
            if any(
                needle in str(value).casefold()
                for value in [launch.get("mint_address"), launch.get("launch_tx"), *launch.get("synonyms", [])]
                if value is not None
            )
        ]
        result = await db.execute(text("""
            WITH indexed AS (
                SELECT s.species_id, s.accepted_name, s.record, s.resolution_status,
                       s.taxon_id AS stored_taxon_id,
                       COALESCE(matches.candidate_count, 0)::int AS candidate_count,
                       matches.candidate_taxon_id, matches.candidate_taxon_ids,
                       t.canonical_name, t.rank AS canonical_rank, t.kingdom AS canonical_kingdom
                FROM fungip.species s
                LEFT JOIN LATERAL (
                    SELECT COUNT(DISTINCT external_id.taxon_id)::int AS candidate_count,
                           (ARRAY_AGG(DISTINCT external_id.taxon_id))[1] AS candidate_taxon_id,
                           ARRAY_AGG(DISTINCT external_id.taxon_id) AS candidate_taxon_ids
                    FROM jsonb_array_elements(
                        CASE WHEN jsonb_typeof(s.external_ids) = 'array' THEN s.external_ids ELSE '[]'::jsonb END
                    ) AS source_identifier(value)
                    JOIN core.taxon_external_id external_id
                      ON external_id.source = source_identifier.value->>'source'
                     AND external_id.external_id = source_identifier.value->>'external_id'
                ) matches ON TRUE
                LEFT JOIN core.taxon t ON t.id = matches.candidate_taxon_id
            )
            SELECT * FROM indexed
            WHERE species_id ILIKE :pattern
               OR accepted_name ILIKE :pattern
               OR record->>'requested_name' ILIKE :pattern
               OR record->>'common_name' ILIKE :pattern
               OR record->>'ticker' ILIKE :pattern
               OR record->'dna'->>'accession_version' ILIKE :pattern
               OR EXISTS (
                   SELECT 1 FROM jsonb_array_elements_text(
                       CASE WHEN jsonb_typeof(record->'synonyms') = 'array'
                            THEN record->'synonyms' ELSE '[]'::jsonb END
                   ) AS synonym(value) WHERE synonym.value ILIKE :pattern
               )
               OR species_id = ANY(:launch_ids)
            ORDER BY CASE WHEN accepted_name ILIKE :exact_query THEN 0 ELSE 1 END, species_id
            LIMIT :limit
        """), {
            "pattern": f"%{query}%", "exact_query": query,
            "launch_ids": launch_ids, "limit": limit,
        })
        output: list[dict[str, Any]] = []
        for raw in result.mappings().all():
            row = dict(raw)
            identity_state, identity_reason, canonical_uuid = project_fungip_identity(row)
            record = _json_object(row.get("record"))
            dna = _json_object(record.get("dna"))
            launch = valid_launches.get(str(row["species_id"]))
            properties: dict[str, Any] = {
                "species_id": str(row["species_id"]),
                "requested_name": record.get("requested_name"),
                "synonyms": merge_source_values(
                    record.get("synonyms"), launch.get("synonyms") if launch else None,
                ),
                "catalog_review_flags": merge_source_values(
                    record.get("catalog_review_flags"),
                    launch.get("catalog_review_flags") if launch else None,
                ),
                "resolution_status": str(row.get("resolution_status") or "unresolved"),
                "identity_state": identity_state,
                "identity_reason": identity_reason,
                "canonical_taxon_uuid": canonical_uuid,
                "candidate_taxon_uuids": [str(value) for value in _json_list(row.get("candidate_taxon_ids"))],
                "ticker": record.get("ticker"),
                "accession_version": dna.get("accession_version"),
            }
            if launch is not None:
                properties["first40_launch"] = launch
            output.append({
                "id": f"fungip:{row['species_id']}",
                "domain": "fungip",
                "entity_type": "fungi_collection_member",
                "name": str(row.get("accepted_name") or row["species_id"]),
                "description": record.get("common_name"),
                "source": "mindex.fungip.species",
                "properties": properties,
            })
        return output, FungiPIndexAvailability(status="available")
    except Exception:
        try:
            await db.rollback()
        except Exception:
            # An optional index failure must not replace its status with a
            # rollback error after the primary search domains have completed.
            pass
        return [], FungiPIndexAvailability(status="error", reason="query_failed")
