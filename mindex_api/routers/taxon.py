from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, pagination_params, require_api_key, PaginationParams
from ..contracts.v1.ancestry_index import (
    FungiPIndexAvailability,
    FungiPIndexCounts,
    FungiPIndexMember,
    FungiPIndexPagination,
    FungiPLaunchAssociation,
    FungiPLaunchIndexState,
    FungiPTaxonIndexResponse,
    FungiPTaxonIndexRow,
)
from ..contracts.v1.taxon import TaxonListResponse, TaxonResponse
from ..services.ancestry_public_members import (
    load_public_fungip_members,
    load_validated_first40_associations,
    merge_source_values,
    project_fungip_identity,
    search_validated_fungip_taxon_ids,
)

router = APIRouter(
    prefix="/taxa",
    tags=["taxa"],
    dependencies=[Depends(require_api_key)],
)

_TAXON_LIST_COLUMNS = """
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
"""


# GBIF/iNat imports were stored with kingdom 'Undesignated'; their real kingdom lives in
# metadata (GBIF `kingdom`, iNat `iconic_taxon_name`). Resolve it at read time so the kingdom
# filter and counts cover every kingdom without rewriting core.taxon.
_UNDESIGNATED_KINGDOMS_SQL = "(kingdom IS NULL OR kingdom IN ('', 'Undesignated'))"
_INAT_ANIMAL_ICONIC_SQL = (
    "('Animalia', 'Insecta', 'Aves', 'Mammalia', 'Reptilia', 'Amphibia', "
    "'Actinopterygii', 'Mollusca', 'Arachnida')"
)
_METADATA_KINGDOM_SQL = (
    "CASE "
    "WHEN NULLIF(metadata->>'kingdom', '') IS NOT NULL THEN metadata->>'kingdom' "
    f"WHEN metadata->>'iconic_taxon_name' IN {_INAT_ANIMAL_ICONIC_SQL} THEN 'Animalia' "
    "ELSE NULLIF(metadata->>'iconic_taxon_name', '') END"
)
_EFFECTIVE_KINGDOM_SQL = (
    f"CASE WHEN {_UNDESIGNATED_KINGDOMS_SQL} "
    f"THEN COALESCE({_METADATA_KINGDOM_SQL}, kingdom) ELSE kingdom END"
)

# Duplicate rows folded into a surviving taxon keep their data and carry metadata.merged_into;
# lists and counts skip them, while direct id lookups still return them.
_ACTIVE_TAXON_SQL = "NOT (COALESCE(metadata, '{}'::jsonb) ? 'merged_into')"


def _csv_values(raw: Optional[str]) -> list[str]:
    values = [v.strip() for v in (raw or "").split(",") if v.strip()]
    return [] if any(v.lower() in ("all", "any") for v in values) else values


def _kingdom_filter_sql(kingdoms: list[str], params: dict[str, Any]) -> str:
    keys = []
    for i, value in enumerate(kingdoms):
        params[f"kingdom_{i}"] = value
        keys.append(f":kingdom_{i}")
    in_list = ", ".join(keys)
    if any(k.lower() == "undesignated" for k in kingdoms):
        return f"({_EFFECTIVE_KINGDOM_SQL}) IN ({in_list})"
    return (
        f"(kingdom IN ({in_list}) OR ({_UNDESIGNATED_KINGDOMS_SQL} "
        f"AND ({_METADATA_KINGDOM_SQL}) IN ({in_list})))"
    )


def _normalize_taxon_row(row: dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    if d.get("metadata") is None:
        d["metadata"] = {}
    if d.get("external_ids") is None:
        d["external_ids"] = {}
    if d.get("author") is None and d.get("authority") is not None:
        d["author"] = d.get("authority")
    return d


_FUNGIP_REQUIRED_PAGE_EVIDENCE = (
    "name_checked", "taxonomy_checked", "dna_checked", "download_checked", "attribution_checked",
)


def _json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _fungip_taxon_index_row(
    raw: dict[str, Any], first40_launch: Optional[dict[str, Any]] = None,
    launch_association_state: Optional[str] = None,
) -> FungiPTaxonIndexRow:
    row = dict(raw)
    record = _json_object(row.get("record"))
    taxonomy = _json_object(record.get("taxonomy"))
    dna = _json_object(record.get("dna"))
    image_errors = any("image" in str(error).lower() for error in _json_list(row.get("validation_errors")))
    image = record.get("image") if row.get("image_valid") and not image_errors else None
    identity_state, identity_reason, canonical_uuid = project_fungip_identity(row)
    evidence = _json_object(row.get("page_evidence"))
    page_is_current = (
        identity_state == "linked"
        and row.get("page_taxon_id") is not None
        and str(row.get("page_taxon_id")) == canonical_uuid
        and row.get("page_record_sha256") == row.get("record_sha256")
        and all(evidence.get(key) is True for key in _FUNGIP_REQUIRED_PAGE_EVIDENCE)
    )
    token_confirmed = bool(row.get("token_confirmed"))
    association = FungiPLaunchAssociation(**first40_launch) if first40_launch is not None else None
    accepted_name = str(row.get("accepted_name") or "")
    source_common_name = record.get("common_name")

    member = FungiPIndexMember(
        species_id=str(row["species_id"]),
        mindex_uuid=canonical_uuid,
        canonical_taxon_uuid=canonical_uuid,
        accepted_name=accepted_name,
        common_name=(row.get("canonical_common_name") if identity_state == "linked" else source_common_name),
        fungal_group=record.get("group"),
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
        resolution_status=str(row.get("resolution_status") or "unresolved"),
        identity_state=identity_state,
        identity_reason=identity_reason,
        canonical_url=row.get("page_canonical_url") if page_is_current else None,
        record_sha256=str(row.get("record_sha256") or ""),
        catalog_sha256=str(row.get("catalog_sha256") or ""),
        validation_errors=_json_list(row.get("validation_errors")),
        source_identifiers=[item for item in _json_list(row.get("external_ids")) if isinstance(item, dict)],
        candidate_taxon_uuids=[UUID(str(value)) for value in _json_list(row.get("candidate_taxon_ids"))],
        taxonomy=taxonomy,
        requested_name=(str(record["requested_name"]) if record.get("requested_name") is not None else None),
        synonyms=merge_source_values(
            record.get("synonyms"), association.synonyms if association is not None else None,
        ),
        catalog_review_flags=merge_source_values(
            record.get("catalog_review_flags"),
            association.catalog_review_flags if association is not None else None,
        ),
        first40_launch=association,
        chain_receipt_status="confirmed" if token_confirmed else "not_confirmed",
        launch_association_state=(
            launch_association_state or ("source_verified" if association is not None else "unavailable")
        ),
    )
    return FungiPTaxonIndexRow(
        id=canonical_uuid,
        canonical_name=row.get("canonical_name") if identity_state == "linked" else None,
        common_name=member.common_name,
        rank="species",
        kingdom="Fungi",
        obs_count=(int(row["obs_count"]) if identity_state == "linked" and row.get("obs_count") is not None else None),
        metadata=_json_object(row.get("canonical_metadata")) if identity_state == "linked" else {},
        fungip=member,
    )


_FUNGIP_INDEX_CTE = """
WITH indexed AS (
    SELECT
        s.species_id, s.taxon_id AS stored_taxon_id, s.accepted_name, s.record,
        s.external_ids, s.verified_its_sequence, s.image_valid, s.sequence_valid,
        s.missing_data_flags, s.validation_errors, s.record_sha256, s.catalog_sha256,
        s.resolution_status,
        COALESCE(matches.candidate_count, 0)::int AS candidate_count,
        matches.candidate_taxon_id,
        matches.candidate_taxon_ids,
        t.id AS canonical_taxon_id, t.canonical_name, t.common_name AS canonical_common_name,
        t.rank AS canonical_rank, t.kingdom AS canonical_kingdom,
        t.metadata AS canonical_metadata,
        CASE
            WHEN s.resolution_status IN ('identity_conflict', 'source_conflict', 'duplicate_canonical')
              OR COALESCE(matches.candidate_count, 0) > 1
              OR (s.resolution_status = 'resolved' AND COALESCE(matches.candidate_count, 0) = 1
                  AND s.taxon_id IS NOT NULL
                  AND s.taxon_id <> matches.candidate_taxon_id)
              OR (s.resolution_status = 'resolved' AND s.taxon_id IS NULL)
              OR (s.resolution_status = 'resolved' AND s.taxon_id IS NOT NULL
                  AND matches.candidate_taxon_id = s.taxon_id AND t.id IS NULL) THEN 'ambiguous'
            WHEN s.resolution_status = 'resolved' AND s.taxon_id IS NOT NULL
              AND COALESCE(matches.candidate_count, 0) = 1
              AND matches.candidate_taxon_id = s.taxon_id AND LOWER(t.rank) = 'species'
              AND COALESCE(LOWER(t.kingdom), '') = 'fungi'
              AND t.canonical_name = s.record->>'accepted_name'
              AND s.accepted_name = s.record->>'accepted_name' THEN 'linked'
            WHEN s.resolution_status = 'resolved' AND COALESCE(matches.candidate_count, 0) = 1
              AND (LOWER(t.rank) IS DISTINCT FROM 'species' OR COALESCE(LOWER(t.kingdom), '') <> 'fungi'
                   OR t.canonical_name IS DISTINCT FROM s.record->>'accepted_name'
                   OR s.accepted_name IS DISTINCT FROM s.record->>'accepted_name') THEN 'ambiguous'
            ELSE 'unresolved'
        END AS identity_state,
        page.taxon_id AS page_taxon_id, page.canonical_url AS page_canonical_url,
        page.record_sha256 AS page_record_sha256, page.evidence AS page_evidence,
        (SELECT COUNT(*)::int FROM obs.observation o WHERE o.taxon_id = t.id) AS obs_count,
        EXISTS (
            SELECT 1 FROM fungip.token_attempt attempt
            WHERE attempt.species_id = s.species_id
              AND attempt.status = 'confirmed'
              AND attempt.network = 'solana-mainnet-beta'
        ) AS token_confirmed
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
    LEFT JOIN fungip.page_verification page
      ON page.species_id = s.species_id AND page.taxon_id = matches.candidate_taxon_id
)
"""


def _fungip_where(has_query: bool, has_prefix: bool, has_first40_search: bool) -> str:
    predicates = []
    if has_query:
        query_options = [
            "indexed.species_id ILIKE :query_pattern",
            "indexed.accepted_name ILIKE :query_pattern",
            "indexed.record->>'requested_name' ILIKE :query_pattern",
            "indexed.record->>'common_name' ILIKE :query_pattern",
            "indexed.record->>'ticker' ILIKE :query_pattern",
            "indexed.record->'dna'->>'accession_version' ILIKE :query_pattern",
            "EXISTS (SELECT 1 FROM jsonb_array_elements_text(CASE "
            "WHEN jsonb_typeof(indexed.record->'synonyms')='array' "
            "THEN indexed.record->'synonyms' ELSE '[]'::jsonb END) AS synonym(value) "
            "WHERE synonym.value ILIKE :query_pattern)",
        ]
        if has_first40_search:
            query_options.append("indexed.species_id = ANY(:first40_search_ids)")
        predicates.append("(" + " OR ".join(query_options) + ")")
    if has_prefix:
        predicates.append("indexed.accepted_name ILIKE :prefix_pattern")
    return " AND ".join(predicates) if predicates else "TRUE"


@router.get("/collections/fungip", response_model=FungiPTaxonIndexResponse)
async def list_fungip_taxon_index(
    q: Optional[str] = Query(None, max_length=512, description="Search FungiP ID, names, ticker, DNA accession, and qualified launch values."),
    offset: int = Query(0, ge=0),
    limit: int = Query(300, ge=1, le=500),
    kingdom: Optional[str] = Query(None),
    rank: Optional[str] = Query(None),
    prefix: Optional[str] = Query(None, max_length=200),
    order_by: str = Query("canonical_name"),
    order: str = Query("asc"),
    db: AsyncSession = Depends(get_db_session),
) -> FungiPTaxonIndexResponse:
    """Read the source collection with exact, optional links to core.taxon UUIDs."""
    normalized_query = (q or "").strip()
    query_pattern = f"%{normalized_query}%" if normalized_query else None
    normalized_kingdom = (kingdom or "").strip().lower()
    kingdom_filter = None if normalized_kingdom in {"", "all", "any"} else normalized_kingdom
    normalized_rank = (rank or "").strip().lower()
    rank_filter = None if normalized_rank in {"", "all", "any"} else normalized_rank
    normalized_prefix = (prefix or "").strip()
    prefix_pattern = f"{normalized_prefix}%" if normalized_prefix else None
    order_by_normalized = (order_by or "").strip().lower()
    order_normalized = (order or "").strip().lower()
    if order_by_normalized not in {"canonical_name", "observations_count", "obs_count"}:
        raise HTTPException(status_code=400, detail="Invalid order_by for FungiP collection.")
    if order_normalized not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="Invalid order for FungiP collection.")
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if query_pattern is not None:
        params["query_pattern"] = query_pattern
    if prefix_pattern is not None:
        params["prefix_pattern"] = prefix_pattern

    try:
        tables = (await db.execute(text("""
            SELECT to_regclass('fungip.species') AS species_table,
                   to_regclass('fungip.first40_launch_association') AS launch_table,
                   to_regclass('fungip.first40_source_batch') AS batch_table
        """))).mappings().one()
        if tables["species_table"] is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="FungiP collection index unavailable; no fallback source is configured.",
            )
        launch_index_available = tables["launch_table"] is not None and tables["batch_table"] is not None
        valid_launches: dict[str, dict[str, Any]] = {}
        invalid_launch_ids: set[str] = set()
        if launch_index_available:
            valid_launches, invalid_launch_ids = await load_validated_first40_associations(db)
        first40_search_ids = []
        if query_pattern is not None:
            needle = normalized_query.casefold()
            first40_search_ids = [
                species_id for species_id, launch in valid_launches.items()
                if any(
                    needle in str(value).casefold()
                    for value in [launch.get("mint_address"), launch.get("launch_tx"), *launch.get("synonyms", [])]
                    if value is not None
                )
            ]
        if first40_search_ids:
            params["first40_search_ids"] = first40_search_ids
        where_sql = (
            "FALSE" if kingdom_filter not in {None, "fungi"} or rank_filter not in {None, "species"}
            else _fungip_where(query_pattern is not None, prefix_pattern is not None, bool(first40_search_ids))
        )
        if order_by_normalized == "canonical_name":
            order_expression = "COALESCE(indexed.canonical_name, indexed.accepted_name)"
        else:
            order_expression = "indexed.obs_count"

        count_result = await db.execute(text(f"""
            {_FUNGIP_INDEX_CTE}
            SELECT COUNT(*)::int AS total,
                   COUNT(*) FILTER (WHERE identity_state = 'linked')::int AS linked,
                   COUNT(*) FILTER (WHERE identity_state = 'unresolved')::int AS unresolved,
                   COUNT(*) FILTER (WHERE identity_state = 'ambiguous')::int AS ambiguous
            FROM indexed
            WHERE {where_sql}
        """), params)
        totals = dict(count_result.mappings().one())

        page_result = await db.execute(text(f"""
            {_FUNGIP_INDEX_CTE}
            SELECT indexed.*
            FROM indexed
            WHERE {where_sql}
            ORDER BY {order_expression} {order_normalized.upper()} NULLS LAST, indexed.species_id ASC
            LIMIT :limit OFFSET :offset
        """), params)
        page_rows = [dict(row) for row in page_result.mappings().all()]
        species_ids = [row["species_id"] for row in page_rows]
        for row in page_rows:
            species_id = str(row["species_id"])
            if species_id in valid_launches:
                row["launch_association_state"] = "source_verified"
            elif species_id in invalid_launch_ids:
                row["launch_association_state"] = "invalid_binding"
            else:
                row["launch_association_state"] = "not_associated" if launch_index_available else "unavailable"

        launch_stats: Optional[dict[str, Any]] = None
        launch_by_species: dict[str, dict[str, Any]] = {
            species_id: valid_launches[species_id]
            for species_id in species_ids if species_id in valid_launches
        }
        if launch_index_available:
            launch_stats_result = await db.execute(text("""
                SELECT COUNT(*)::int AS associations,
                       (SELECT COUNT(DISTINCT superseded_mint)::int
                        FROM fungip.first40_launch_association association
                        CROSS JOIN LATERAL unnest(association.superseded_mints) AS superseded(superseded_mint)
                       ) AS exclusions
                FROM fungip.first40_launch_association
            """))
            launch_stats = dict(launch_stats_result.mappings().one())
        data = [
            _fungip_taxon_index_row(
                row,
                launch_by_species.get(str(row["species_id"])),
                str(row.get("launch_association_state") or "unavailable"),
            )
            for row in page_rows
        ]
        launch_state = FungiPLaunchIndexState(
            state="available" if launch_index_available else "unavailable",
            verification_basis=(
                "user_supplied_and_attached_verification" if launch_index_available else None
            ),
            new_chain_verified=False,
        )
        return FungiPTaxonIndexResponse(
            collection="FungiP 300",
            data=data,
            pagination=FungiPIndexPagination(
                limit=limit, offset=offset, total=int(totals.get("total") or 0),
            ),
            counts=FungiPIndexCounts(
                listed=int(totals.get("total") or 0),
                linked=int(totals.get("linked") or 0),
                unresolved=int(totals.get("unresolved") or 0),
                ambiguous=int(totals.get("ambiguous") or 0),
                launch_associations=(int(launch_stats["associations"]) if launch_stats is not None else None),
                superseded_exclusions=(int(launch_stats["exclusions"]) if launch_stats is not None else None),
            ),
            launch_index=launch_state,
        )
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FungiP collection index unavailable; no fallback source is configured.",
        ) from exc


async def _list_taxa_query(
    db: AsyncSession,
    *,
    from_clause: str,
    where_sql: str,
    params: dict[str, Any],
    order_expr: str,
    order_normalized: str,
) -> tuple[list[dict[str, Any]], int, str]:
    stmt = text(
        f"""
        SELECT {_TAXON_LIST_COLUMNS}
        FROM {from_clause}
        WHERE {where_sql}
        ORDER BY {order_expr} {order_normalized}, canonical_name ASC
        LIMIT :limit OFFSET :offset
        """
    )
    result = await db.execute(stmt, params)
    rows = [_normalize_taxon_row(dict(row)) for row in result.mappings().all()]
    total, count_cache_state = await _cached_count_with_state(
        db, from_clause=from_clause, where_sql=where_sql, params=params,
    )
    return rows, total, count_cache_state


# Bounded TTL cache for list totals and stats: counts over millions of rows are re-used
# across pages; a short TTL keeps totals honest while bulk ingestion is running.
_COUNT_CACHE_TTL_SECONDS = 300
_COUNT_CACHE_MAX_ENTRIES = 1024
_count_cache: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()


def _cache_get(key: str) -> Any:
    hit = _count_cache.get(key)
    if hit is None or time.monotonic() - hit[0] > _COUNT_CACHE_TTL_SECONDS:
        _count_cache.pop(key, None)
        return None
    _count_cache.move_to_end(key)
    return hit[1]


def _cache_put(key: str, value: Any) -> None:
    _count_cache[key] = (time.monotonic(), value)
    _count_cache.move_to_end(key)
    while len(_count_cache) > _COUNT_CACHE_MAX_ENTRIES:
        _count_cache.popitem(last=False)


async def _cached_count(db: AsyncSession, *, from_clause: str, where_sql: str, params: dict[str, Any]) -> int:
    total, _ = await _cached_count_with_state(
        db, from_clause=from_clause, where_sql=where_sql, params=params,
    )
    return total


async def _cached_count_with_state(
    db: AsyncSession, *, from_clause: str, where_sql: str, params: dict[str, Any],
) -> tuple[int, str]:
    filters = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    key = json.dumps([from_clause, where_sql, filters], sort_keys=True, default=str)
    cached = _cache_get(key)
    if cached is not None:
        return cached, "cache_hit"
    count_result = await db.execute(text(f"SELECT count(*) FROM {from_clause} WHERE {where_sql}"), filters)
    total = int(count_result.scalar_one() or 0)
    _cache_put(key, total)
    return total, "fresh_query"


# Sources abbreviate ranks differently (MycoBank: "sp.", "gen.", ...). A rank filter matches every
# spelling of the same rank so species totals include all sources.
_RANK_ALIASES: dict[str, tuple[str, ...]] = {
    "species": ("species", "sp."),
    "genus": ("genus", "gen."),
    "family": ("family", "fam."),
    "subspecies": ("subspecies", "subsp."),
    "variety": ("variety", "var."),
    "form": ("form", "f."),
    "section": ("section", "sect."),
    "subgenus": ("subgenus", "subgen."),
    "order": ("order", "ord."),
    "class": ("class", "cl."),
    "phylum": ("phylum", "div.", "phyl."),
}


def _rank_variants(rank: str) -> list[str]:
    normalized = rank.strip().lower()
    for canonical, variants in _RANK_ALIASES.items():
        if normalized in variants:
            return list(variants)
    return [rank.strip()]


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_EXPLICIT_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "edible": ("edible", "choice", "choice edible"),
    "medicinal": ("medicinal",),
    "poisonous": ("poisonous", "deadly", "toxic", "deadly poisonous"),
    "psychoactive": ("psychoactive", "hallucinogenic"),
    "gourmet": ("gourmet", "choice", "choice edible"),
}
_ALL_EXPLICIT_CATEGORY_VALUES = tuple(dict.fromkeys(
    value for aliases in _EXPLICIT_CATEGORY_ALIASES.values() for value in aliases
))


def _normalized_tag_sql(value_sql: str) -> str:
    """Match the explorer's explicit-tag normalization without deriving biology."""
    return f"regexp_replace(regexp_replace(lower(btrim({value_sql})), '[_-]+', ' ', 'g'), '[[:space:]]+', ' ', 'g')"


def _category_match_sql(values: tuple[str, ...], prefix: str) -> tuple[str, dict[str, Any]]:
    names = []
    params: dict[str, Any] = {}
    for index, value in enumerate(values):
        key = f"{prefix}_{index}"
        names.append(f":{key}")
        params[key] = value
    in_values = ", ".join(names)
    normalized_metadata_edibility = _normalized_tag_sql("t.metadata->>'edibility'")
    normalized_metadata_characteristic = _normalized_tag_sql("metadata_tag.value")
    normalized_trait = _normalized_tag_sql("trait.value_text")
    normalized_characteristic = _normalized_tag_sql("characteristic.value_text")
    return (
        "(" + " OR ".join((
            f"COALESCE({normalized_metadata_edibility} IN ({in_values}), FALSE)",
            f"EXISTS (SELECT 1 FROM jsonb_array_elements_text(CASE "
            f"WHEN jsonb_typeof(t.metadata->'characteristics') = 'array' "
            f"THEN t.metadata->'characteristics' ELSE '[]'::jsonb END) AS metadata_tag(value) "
            f"WHERE {normalized_metadata_characteristic} IN ({in_values}))",
            f"EXISTS (SELECT 1 FROM bio.taxon_trait trait WHERE trait.taxon_id = t.id "
            f"AND NULLIF(btrim(trait.source), '') IS NOT NULL "
            f"AND lower(trait.trait_name) IN ('edibility', 'characteristic', 'characteristics') "
            f"AND {normalized_trait} IN ({in_values}))",
            f"EXISTS (SELECT 1 FROM bio.taxon_characteristic characteristic "
            f"WHERE characteristic.taxon_id = t.id AND NULLIF(btrim(characteristic.source), '') IS NOT NULL "
            f"AND lower(characteristic.name) IN ('edibility', 'characteristic', 'characteristics') "
            f"AND {normalized_characteristic} IN ({in_values}))",
        )) + ")",
        params,
    )


def _valid_photo_url_sql(url_sql: str) -> str:
    url_sql = f"btrim({url_sql})"
    return (
        f"({url_sql} ~* '^https?://[^[:space:]]+$' OR "
        f"({url_sql} LIKE '/%' AND {url_sql} NOT LIKE '//%')) "
        f"AND {url_sql} !~* 'placeholder\\.(svg|png|jpe?g)([?#]|$)' "
        f"AND {url_sql} !~ '[[:cntrl:]]' "
        f"AND strpos(COALESCE({url_sql}, ''), chr(92)) = 0 "
        f"AND {url_sql} !~* '^https?://[^/]*@'"
    )


def _core_photo_exists_sql() -> str:
    urls = [
        "t.metadata->'default_photo'->>'medium_url'",
        "t.metadata->'default_photo'->>'url'",
        "t.metadata->'photos'->0->>'url'",
    ]
    return "(" + " OR ".join(_valid_photo_url_sql(url) for url in urls) + ")"


def _has_images_candidate_sql(*, fungip_available: bool) -> str:
    """Superset of the exact photo predicates, shaped so the metadata GIN index bounds the scan.

    Every core photo URL lives under a top-level default_photo/photos key and every FungiP image
    requires a valid image row keyed by taxon_id, so ANDing this never changes which taxa match.
    """
    return f"t.id IN ({_photo_candidate_ids_sql(fungip_available=fungip_available)})"


def _photo_candidate_ids_sql(*, fungip_available: bool) -> str:
    candidates = "SELECT c.id FROM core.taxon c WHERE c.metadata ?| array['default_photo', 'photos']"
    if fungip_available:
        candidates += (
            " UNION SELECT source.taxon_id FROM fungip.species source "
            "WHERE source.image_valid IS TRUE AND source.taxon_id IS NOT NULL"
        )
    return candidates


def _fungip_photo_exists_sql() -> str:
    url = "COALESCE(source.record->'image'->>'image_url', source.record->'image'->>'url')"
    return _fungip_linked_record_sql(
        "source.image_valid IS TRUE "
        "AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(CASE "
        "WHEN jsonb_typeof(source.validation_errors) = 'array' THEN source.validation_errors "
        "ELSE '[]'::jsonb END) error(value) WHERE error.value ILIKE '%image%') "
        f"AND {_valid_photo_url_sql(url)}"
    )


def _fungip_linked_record_sql(record_predicate: str) -> str:
    """Restrict optional FungiP evidence to the same exact validated identity used by its public projection."""
    return f"""EXISTS (
        SELECT 1
        FROM fungip.species source
        JOIN LATERAL (
            SELECT COUNT(DISTINCT external_id.taxon_id)::int AS candidate_count,
                   (ARRAY_AGG(DISTINCT external_id.taxon_id))[1] AS candidate_taxon_id
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(source.external_ids) = 'array' THEN source.external_ids ELSE '[]'::jsonb END
            ) AS source_identifier(value)
            JOIN core.taxon_external_id external_id
              ON external_id.source = source_identifier.value->>'source'
             AND external_id.external_id = source_identifier.value->>'external_id'
        ) matches ON TRUE
        WHERE source.taxon_id = t.id
          AND source.resolution_status = 'resolved'
          AND matches.candidate_count = 1
          AND matches.candidate_taxon_id = t.id
          AND lower(t.rank) = 'species' AND lower(t.kingdom) = 'fungi'
          AND t.canonical_name = source.record->>'accepted_name'
          AND source.accepted_name = source.record->>'accepted_name'
          AND {record_predicate}
    )"""


def _fungip_family_join_sql() -> str:
    """Resolve one deterministic family from an exact validated FungiP identity."""
    return """LEFT JOIN LATERAL (
        SELECT NULLIF(btrim(source.record->'taxonomy'->>'family'), '') AS family,
               source.species_id
        FROM fungip.species source
        JOIN LATERAL (
            SELECT COUNT(DISTINCT external_id.taxon_id)::int AS candidate_count,
                   (ARRAY_AGG(DISTINCT external_id.taxon_id))[1] AS candidate_taxon_id
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(source.external_ids) = 'array'
                     THEN source.external_ids ELSE '[]'::jsonb END
            ) AS source_identifier(value)
            JOIN core.taxon_external_id external_id
              ON external_id.source = source_identifier.value->>'source'
             AND external_id.external_id = source_identifier.value->>'external_id'
        ) matches ON TRUE
        WHERE source.taxon_id = t.id AND source.resolution_status = 'resolved'
          AND matches.candidate_count = 1 AND matches.candidate_taxon_id = t.id
          AND lower(t.rank) = 'species' AND lower(t.kingdom) = 'fungi'
          AND t.canonical_name = source.record->>'accepted_name'
          AND source.accepted_name = source.record->>'accepted_name'
          AND NULLIF(btrim(source.record->'taxonomy'->>'family'), '') IS NOT NULL
        ORDER BY source.species_id ASC
        LIMIT 1
    ) fungip_family ON TRUE"""


def _family_value_sql(*, fungip_available: bool) -> str:
    core_family = "NULLIF(btrim(t.metadata->>'family'), '')"
    if fungip_available:
        return f"COALESCE({core_family}, fungip_family.family, 'Unknown')"
    return f"COALESCE({core_family}, 'Unknown')"


def _safe_image_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if len(candidate) > 2048 or re.search(r"[\\\x00-\x1f\x7f]", candidate):
        return None
    if re.search(r"placeholder\.(?:svg|png|jpe?g)(?:[?#]|$)", candidate, re.IGNORECASE):
        return None
    if candidate.startswith("/"):
        return candidate if not candidate.startswith("//") else None
    try:
        parsed = urlsplit(candidate)
        return candidate if parsed.scheme.lower() in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def _project_category_evidence(raw: Any) -> tuple[list[dict[str, str]], bool]:
    if not isinstance(raw, list):
        return [], False
    truncated = len(raw) > 64
    projected: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw[:64]:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        value = item.get("value")
        if source not in {
            "core.taxon.metadata.edibility",
            "core.taxon.metadata.characteristics",
            "bio.taxon_trait",
            "bio.taxon_characteristic",
        } or not isinstance(value, str) or not value.strip():
            continue
        normalized = re.sub(r"\s+", " ", re.sub(r"[_-]+", " ", value.strip().lower()))
        for category, aliases in _EXPLICIT_CATEGORY_ALIASES.items():
            if normalized not in aliases:
                continue
            key = (category, source, normalized)
            if key in seen:
                continue
            if len(projected) >= 64:
                truncated = True
                return projected, truncated
            seen.add(key)
            projected.append({"category": category, "source": source, "value": value.strip()[:120]})
    return projected, truncated


def _project_family(row: dict[str, Any], member: Optional[FungiPIndexMember]) -> None:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    core_family_raw = metadata.get("family")
    core_family = core_family_raw.strip() if isinstance(core_family_raw, str) and core_family_raw.strip() else None
    taxonomy = member.taxonomy if member is not None and isinstance(member.taxonomy, dict) else {}
    source_family_raw = taxonomy.get("family")
    source_family = source_family_raw.strip() if isinstance(source_family_raw, str) and source_family_raw.strip() else None
    row["family"] = core_family or source_family or "Unknown"
    row["family_source"] = (
        "core.taxon.metadata.family" if core_family else
        "fungip.species.record.taxonomy.family" if source_family else "unknown"
    )
    evidence = []
    if core_family:
        evidence.append({"source": "core.taxon.metadata.family", "value": core_family[:200]})
    if source_family:
        evidence.append({
            "source": "fungip.species.record.taxonomy.family", "value": source_family[:200],
            "species_id": member.species_id,
        })
    row["family_evidence"] = evidence


def _project_image_selection(row: dict[str, Any], member: Optional[FungiPIndexMember]) -> Optional[dict[str, Any]]:
    def selection(photo: Any, url_key: str, source: str) -> Optional[dict[str, Any]]:
        if not isinstance(photo, dict):
            return None
        url = _safe_image_url(photo.get(url_key))
        if not url:
            return None
        source_url = (
            _safe_image_url(photo.get("source_url"))
            or _safe_image_url(photo.get("source_page"))
            or _safe_image_url(photo.get("url"))
            or url
        )
        attribution = photo.get("attribution")
        license_code = photo.get("license_code")
        return {
            "url": url,
            "source": source,
            "attribution": attribution.strip()[:512] if isinstance(attribution, str) and attribution.strip() else None,
            "license_code": license_code.strip()[:128] if isinstance(license_code, str) and license_code.strip() else None,
            "source_url": source_url,
        }

    if member is not None:
        image = member.image if isinstance(member.image, dict) else None
        selected = selection(image, "image_url", "fungip.species.record.image.image_url")
        if selected is None:
            selected = selection(image, "url", "fungip.species.record.image.url")
        if selected:
            return selected
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    default_photo = metadata.get("default_photo") if isinstance(metadata.get("default_photo"), dict) else {}
    for key in ("medium_url", "url"):
        selected = selection(default_photo, key, f"core.taxon.metadata.default_photo.{key}")
        if selected:
            return selected
    photos = metadata.get("photos")
    if isinstance(photos, list) and photos:
        return selection(photos[0], "url", "core.taxon.metadata.photos[0].url")
    return None


_PAGE_COUNT_COLUMNS = """
            (SELECT COUNT(*)::bigint FROM obs.observation o WHERE o.taxon_id = page.id) AS obs_count,
            (SELECT COUNT(*)::bigint FROM media.image i WHERE i.taxon_id = page.id) AS image_count,
            (SELECT COUNT(*)::bigint FROM media.video v WHERE v.taxon_id = page.id) AS video_count,
            (SELECT COUNT(*)::bigint FROM media.audio a WHERE a.taxon_id = page.id) AS audio_count,
            (SELECT COUNT(*)::bigint FROM bio.genome g WHERE g.taxon_id = page.id) AS genome_count,
            (SELECT COUNT(*)::bigint FROM bio.taxon_compound tc WHERE tc.taxon_id = page.id) AS compound_link_count,
            (SELECT COUNT(*)::bigint FROM bio.taxon_interaction ti
             WHERE ti.source_taxon_id = page.id OR ti.target_taxon_id = page.id) AS interaction_count,
            (SELECT COUNT(*)::bigint FROM bio.publication_taxon pt WHERE pt.taxon_id = page.id) AS publication_count,
            (SELECT COUNT(*)::bigint FROM bio.taxon_characteristic c WHERE c.taxon_id = page.id) AS characteristic_count
"""

_PAGE_CATEGORY_EVIDENCE_SQL = """
            (
                SELECT COALESCE(
                    jsonb_agg(evidence_rows.payload ORDER BY evidence_rows.source, evidence_rows.value),
                    '[]'::jsonb
                )
                FROM (
                    SELECT recognized.source, recognized.value,
                           jsonb_build_object('source', recognized.source, 'value', recognized.value) AS payload
                    FROM (
                        SELECT 'core.taxon.metadata.edibility'::text AS source,
                               __NORMALIZED_METADATA_EDIBILITY__ AS value
                        UNION ALL
                        SELECT 'core.taxon.metadata.characteristics', __NORMALIZED_METADATA_CHARACTERISTIC__
                        FROM jsonb_array_elements_text(CASE
                            WHEN jsonb_typeof(page.metadata->'characteristics') = 'array'
                            THEN page.metadata->'characteristics' ELSE '[]'::jsonb END) AS metadata_tag(value)
                        UNION ALL
                        SELECT 'bio.taxon_trait', __NORMALIZED_TRAIT__
                        FROM bio.taxon_trait trait
                        WHERE trait.taxon_id = page.id
                          AND NULLIF(btrim(trait.source), '') IS NOT NULL
                          AND lower(trait.trait_name) IN ('edibility', 'characteristic', 'characteristics')
                        UNION ALL
                        SELECT 'bio.taxon_characteristic', __NORMALIZED_CHARACTERISTIC__
                        FROM bio.taxon_characteristic characteristic
                        WHERE characteristic.taxon_id = page.id
                          AND NULLIF(btrim(characteristic.source), '') IS NOT NULL
                          AND lower(characteristic.name) IN ('edibility', 'characteristic', 'characteristics')
                    ) recognized
                    WHERE recognized.value = ANY(:category_evidence_values)
                    ORDER BY recognized.source, recognized.value
                    LIMIT 65
                ) evidence_rows
            ) AS category_evidence_raw
"""

_METADATA_OBS_EXPR = (
    "CASE WHEN (metadata->>'observations_count') ~ '^[0-9]+$' "
    "THEN (metadata->>'observations_count')::bigint ELSE 0 END"
)
_OBSERVATION_COUNT_SQL = "(SELECT COUNT(*)::bigint FROM obs.observation observation WHERE observation.taxon_id = t.id)"


def _popularity_head_sql(*, photo_sql: str, fungip_available: bool) -> str:
    """Rows whose (observation count, photo) popularity key is above (0, no photo).

    Every other row ties on that key, so it orders by canonical_name, id alone. The UNION of
    observed and photo-candidate ids is a superset of the head, so the planner probes those
    primary keys instead of computing the count and photo regexes for every species.
    """
    candidates = (
        "SELECT observation.taxon_id FROM obs.observation observation WHERE observation.taxon_id IS NOT NULL "
        f"UNION {_photo_candidate_ids_sql(fungip_available=fungip_available)}"
    )
    return f"(t.id IN ({candidates}) AND ({_OBSERVATION_COUNT_SQL} > 0 OR {photo_sql}))"


async def _list_taxa_core_page(
    db: AsyncSession,
    *,
    where_sql: str,
    params: dict[str, Any],
    by_popularity: bool,
    order_normalized: str,
    order_expr_override: Optional[str] = None,
    order_source_sql: str = "",
    secondary_order_expr: Optional[str] = None,
    popularity_head_sql: Optional[str] = None,
    include_category_evidence: bool = False,
) -> tuple[list[dict[str, Any]], int, str]:
    """Filter, sort and page on core.taxon first; per-taxon counts only for the returned page.

    With ``popularity_head_sql`` the popularity order is paged as consecutive segments: the
    small head ordered by the full key, and the tied remainder ordered by canonical_name, id.
    The concatenation is exactly the single ORDER BY over all matching rows.
    """
    order_expr = order_expr_override or (_OBSERVATION_COUNT_SQL if by_popularity else "canonical_name")
    secondary_select = f", {secondary_order_expr} AS secondary_sort_key" if secondary_order_expr else ""
    secondary_inner_order = f", {secondary_order_expr} DESC" if secondary_order_expr else ""
    secondary_outer_order = ", page.secondary_sort_key DESC" if secondary_order_expr else ""
    full_order_sql = f"{order_expr} {order_normalized}{secondary_inner_order}, canonical_name ASC, id ASC"
    full_keys_sql = f"{order_expr} AS sort_key{secondary_select}"
    category_evidence_sql = "NULL::jsonb AS category_evidence_raw"
    if include_category_evidence:
        category_evidence_sql = _PAGE_CATEGORY_EVIDENCE_SQL.replace(
            "__NORMALIZED_METADATA_EDIBILITY__", _normalized_tag_sql("page.metadata->>'edibility'"),
        ).replace(
            "__NORMALIZED_METADATA_CHARACTERISTIC__", _normalized_tag_sql("metadata_tag.value"),
        ).replace(
            "__NORMALIZED_TRAIT__", _normalized_tag_sql("trait.value_text"),
        ).replace(
            "__NORMALIZED_CHARACTERISTIC__", _normalized_tag_sql("characteristic.value_text"),
        )
    page_params = dict(params)
    if include_category_evidence:
        page_params["category_evidence_values"] = list(_ALL_EXPLICIT_CATEGORY_VALUES)

    async def fetch_segment(
        segment_where: str, keys_sql: str, order_sql: str, limit: int, offset: int,
    ) -> list[dict[str, Any]]:
        stmt = text(
            f"""
            SELECT page.id, page.canonical_name, page.rank, page.common_name, page.author, page.description,
                   page.source, page.metadata, page.kingdom, page.lineage, page.lineage_ids, page.external_ids,
                   page.created_at, page.updated_at,
                   {category_evidence_sql},
                   {_PAGE_COUNT_COLUMNS}
            FROM (
                SELECT id, canonical_name, rank, common_name, COALESCE(author, authority) AS author,
                       description, source, metadata, {_EFFECTIVE_KINGDOM_SQL} AS kingdom,
                       lineage, lineage_ids, external_ids, created_at, updated_at,
                       {keys_sql}
                FROM core.taxon t
                {order_source_sql}
                WHERE {segment_where}
                ORDER BY {order_sql}
                LIMIT :limit OFFSET :offset
            ) page
            ORDER BY page.sort_key {order_normalized}{secondary_outer_order}, page.canonical_name ASC, page.id ASC
            """
        )
        result = await db.execute(stmt, {**page_params, "limit": limit, "offset": offset})
        return [dict(row) for row in result.mappings().all()]

    if not (by_popularity and popularity_head_sql and order_expr_override is None):
        raw_rows = await fetch_segment(
            where_sql, full_keys_sql, full_order_sql, int(params["limit"]), int(params["offset"]),
        )
    else:
        tied_keys_sql = "0::bigint AS sort_key" + (", 0 AS secondary_sort_key" if secondary_order_expr else "")
        head = f"({where_sql}) AND {popularity_head_sql}"
        tail = (f"({where_sql}) AND NOT {popularity_head_sql}", tied_keys_sql, "canonical_name ASC, id ASC")
        if order_normalized == "desc":
            segments = [(head, full_keys_sql, full_order_sql), tail]
        else:
            # Ascending count still ranks photo rows first among zero-observation ties.
            segments = [
                (f"{head} AND {_OBSERVATION_COUNT_SQL} = 0", full_keys_sql, full_order_sql),
                tail,
                (f"{head} AND {_OBSERVATION_COUNT_SQL} > 0", full_keys_sql, full_order_sql),
            ]
        raw_rows = []
        remaining = int(params["limit"])
        offset = int(params["offset"])
        for index, (segment_where, keys_sql, order_sql) in enumerate(segments):
            if remaining <= 0:
                break
            segment_rows = await fetch_segment(segment_where, keys_sql, order_sql, remaining, offset)
            raw_rows.extend(segment_rows)
            remaining -= len(segment_rows)
            if segment_rows or offset == 0:
                offset = 0
            elif index < len(segments) - 1:
                # The requested offset lies past this whole segment; skip it by its size.
                skipped = await db.execute(
                    text(f"SELECT count(*) FROM core.taxon t {order_source_sql} WHERE {segment_where}"),
                    {k: v for k, v in params.items() if k not in ("limit", "offset")},
                )
                offset = max(0, offset - int(skipped.scalar_one() or 0))

    rows = [_normalize_taxon_row(row) for row in raw_rows]
    for row in rows:
        evidence, truncated = _project_category_evidence(row.pop("category_evidence_raw", None))
        row["category_evidence"] = evidence
        row["category_evidence_truncated"] = truncated
    count_from_clause = f"core.taxon t {order_source_sql}" if order_source_sql else "core.taxon t"
    total, count_cache_state = await _cached_count_with_state(
        db, from_clause=count_from_clause, where_sql=where_sql, params=params,
    )
    return rows, total, count_cache_state


@router.get("/stats")
async def taxa_stats(db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """Real stored species totals by kingdom and source (cached briefly; ingestion may be running)."""
    cached = _cache_get("taxa_stats")
    if cached is not None:
        return cached
    species_ranks = list(_RANK_ALIASES["species"])
    try:
        kingdom_rows = (await db.execute(
            text(
                "SELECT COALESCE(kingdom, 'Undesignated') AS kingdom, count(*)::bigint AS species "
                f"FROM core.taxon WHERE rank = ANY(:ranks) AND {_ACTIVE_TAXON_SQL} GROUP BY 1 ORDER BY 2 DESC"
            ),
            {"ranks": species_ranks},
        )).mappings().all()
        primary_rows = (await db.execute(
            text(
                "SELECT COALESCE(source, 'unknown') AS source, count(*)::bigint AS species "
                f"FROM core.taxon WHERE rank = ANY(:ranks) AND {_ACTIVE_TAXON_SQL} GROUP BY 1 ORDER BY 2 DESC"
            ),
            {"ranks": species_ranks},
        )).mappings().all()
        linked_rows = (await db.execute(
            text(
                "SELECT x.source, count(DISTINCT x.taxon_id)::bigint AS species "
                "FROM core.taxon_external_id x JOIN core.taxon t ON t.id = x.taxon_id "
                "WHERE t.rank = ANY(:ranks) AND NOT (COALESCE(t.metadata, '{}'::jsonb) ? 'merged_into') "
                "GROUP BY 1 ORDER BY 2 DESC"
            ),
            {"ranks": species_ranks},
        )).mappings().all()
        taxa_total = int((await db.execute(text("SELECT count(*) FROM core.taxon"))).scalar_one() or 0)
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Taxon stats unavailable.")
    by_kingdom = [{"kingdom": r["kingdom"], "species": int(r["species"])} for r in kingdom_rows]
    payload = {
        "species_total": sum(row["species"] for row in by_kingdom),
        "taxa_total": taxa_total,
        "species_ranks": species_ranks,
        "by_kingdom": by_kingdom,
        "by_primary_source": [{"source": r["source"], "species": int(r["species"])} for r in primary_rows],
        "by_linked_source": [{"source": r["source"], "species": int(r["species"])} for r in linked_rows],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_ttl_seconds": _COUNT_CACHE_TTL_SECONDS,
    }
    _cache_put("taxa_stats", payload)
    return payload


@router.get("/kingdom-counts")
async def taxa_kingdom_counts(
    rank: Optional[str] = Query(
        None, description="Optional rank filter; comma-separated, abbreviations match (species also matches 'sp.')."
    ),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Live per-kingdom taxon counts with GBIF/iNat 'Undesignated' rows resolved to their real kingdom."""
    ranks = _csv_values(rank)
    variants = list(dict.fromkeys(v for value in ranks for v in _rank_variants(value)))
    cache_key = "kingdom_counts:" + (",".join(variants) or "*")
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    params: dict[str, Any] = {}
    where_sql = _ACTIVE_TAXON_SQL
    if variants:
        where_sql = f"rank = ANY(:rank_variants) AND {_ACTIVE_TAXON_SQL}"
        params["rank_variants"] = variants
    try:
        rows = (await db.execute(
            text(
                f"SELECT COALESCE({_EFFECTIVE_KINGDOM_SQL}, 'Undesignated') AS kingdom, count(*)::bigint AS taxon_count "
                f"FROM core.taxon WHERE {where_sql} GROUP BY 1 ORDER BY 2 DESC"
            ),
            params,
        )).mappings().all()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Kingdom counts unavailable.")
    kingdoms = [{"kingdom": r["kingdom"], "taxon_count": int(r["taxon_count"])} for r in rows]
    payload = {
        "kingdoms": kingdoms,
        "total": sum(k["taxon_count"] for k in kingdoms),
        "rank": variants or None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_ttl_seconds": _COUNT_CACHE_TTL_SECONDS,
    }
    _cache_put(cache_key, payload)
    return payload


@router.get("", response_model=TaxonListResponse)
async def list_taxa(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    ids: Optional[str] = Query(None, description="Comma-separated taxon UUIDs for batch lookup (e.g., ?ids=uuid1,uuid2)."),
    q: Optional[str] = Query(
        None,
        description="Free-text search across canonical/common names and exact linked FungiP ID, ticker, or DNA accession.",
    ),
    rank: Optional[str] = Query(None, description="Rank filter; abbreviations match (species also matches 'sp.')."),
    source: Optional[str] = Query(None, description="Exact source filter (e.g., inat, gbif, mycobank)."),
    prefix: Optional[str] = Query(None, description="Prefix match on canonical_name (e.g., 'A' for A*)."),
    kingdom: Optional[str] = Query(
        None,
        description=(
            "Filter by kingdom (Fungi, Plantae, Animalia, ...); comma-separated for several. "
            "GBIF/iNat rows stored as 'Undesignated' match their metadata kingdom. Omit for all kingdoms."
        ),
    ),
    lineage_contains: Optional[str] = Query(
        None,
        description="Match if any name in the materialized lineage array contains this substring (case-insensitive).",
    ),
    family: Optional[str] = Query(
        None, max_length=200,
        description="Exact persisted family value from core metadata or an exact validated FungiP source link.",
    ),
    category: Optional[str] = Query(
        None,
        description="Explicit persisted trait category: edible, medicinal, poisonous, psychoactive, gourmet, or unknown.",
    ),
    filter: Optional[str] = Query(
        None, alias="filter", description="Data completeness: has_images or has_description."
    ),
    order_by: str = Query(
        "canonical_name",
        description="Sort field: canonical_name, observations_count, family, or featured.",
    ),
    order: str = Query("asc", description="Sort order. Allowed: asc, desc."),
) -> TaxonListResponse:
    # Build dynamic WHERE clause to avoid asyncpg NULL parameter issues
    where_clauses = []
    params: dict = {
        "limit": pagination.limit,
        "offset": pagination.offset,
    }
    fungip_search_status: Optional[FungiPIndexAvailability] = None

    if q and q.strip():
        q_pattern = f"%{_like_escape(q.strip())}%"
        where_clauses.append("(canonical_name ILIKE :q_pattern OR common_name ILIKE :q_pattern")
        params["q_pattern"] = q_pattern
        fungip_taxon_ids, fungip_search_status = await search_validated_fungip_taxon_ids(db, q_pattern)
        if fungip_taxon_ids:
            where_clauses[-1] += " OR id = ANY(CAST(:fungip_taxon_ids AS uuid[]))"
            params["fungip_taxon_ids"] = fungip_taxon_ids
        where_clauses[-1] += ")"
    rank_variants = list(dict.fromkeys(v for value in _csv_values(rank) for v in _rank_variants(value)))
    if rank_variants:
        where_clauses.append("rank = ANY(:rank_variants)")
        params["rank_variants"] = rank_variants
    if source:
        where_clauses.append("source = :source")
        params["source"] = source
    if prefix and prefix.strip():
        where_clauses.append("lower(canonical_name) LIKE :prefix_pattern")
        params["prefix_pattern"] = f"{_like_escape(prefix.strip().lower())}%"
    id_list = [x.strip() for x in ids.split(",") if x.strip()] if ids else []
    if id_list:
        where_clauses.append("id = ANY(CAST(STRING_TO_ARRAY(:ids_csv, ',') AS uuid[]))")
        params["ids_csv"] = ",".join(id_list)
    else:
        where_clauses.append(_ACTIVE_TAXON_SQL)
    kingdoms = _csv_values(kingdom)
    if kingdoms:
        where_clauses.append(_kingdom_filter_sql(kingdoms, params))
    if lineage_contains and lineage_contains.strip():
        where_clauses.append(
            "EXISTS (SELECT 1 FROM unnest(COALESCE(lineage, ARRAY[]::text[])) x "
            "WHERE x ILIKE :lcp)"
        )
        params["lcp"] = f"%{lineage_contains.strip()}%"

    normalized_family = (family or "").strip()
    normalized_category = (category or "").strip().lower()
    normalized_completeness = (filter or "").strip().lower()
    if normalized_category and normalized_category != "all":
        if normalized_category == "unknown":
            known_sql, known_params = _category_match_sql(_ALL_EXPLICIT_CATEGORY_VALUES, "known_category")
            where_clauses.append(f"NOT {known_sql}")
            params.update(known_params)
        elif normalized_category in _EXPLICIT_CATEGORY_ALIASES:
            category_sql, category_params = _category_match_sql(
                _EXPLICIT_CATEGORY_ALIASES[normalized_category], "category"
            )
            where_clauses.append(category_sql)
            params.update(category_params)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unsupported category. Allowed: edible, medicinal, poisonous, psychoactive, gourmet, unknown.",
            )

    if normalized_completeness not in {"", "all", "has_images", "has_description"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported completeness filter. Allowed: has_images, has_description.",
        )

    order_by_normalized = (order_by or "").strip().lower()
    needs_fungip_fields = (
        bool(normalized_family)
        or normalized_completeness == "has_images"
        or order_by_normalized in {"family", "family-asc"}
        or order_by_normalized in {"observations", "observations_count", "obs_count"}
    )
    fungip_available = True
    if needs_fungip_fields:
        try:
            fungip_available = (
                await db.execute(text("SELECT to_regclass('fungip.species')"))
            ).scalar_one_or_none() is not None
        except Exception as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Optional source-qualified FungiP filter state unavailable.",
            ) from exc

    family_join = _fungip_family_join_sql() if fungip_available and (normalized_family or order_by_normalized in {"family", "family-asc"}) else ""
    family_value_sql = _family_value_sql(fungip_available=bool(family_join))
    if normalized_family:
        params["family"] = normalized_family
        where_clauses.append(f"{family_value_sql} = :family")

    if normalized_completeness == "has_description":
        where_clauses.append(
            "COALESCE(NULLIF(btrim(t.description), ''), NULLIF(btrim(t.metadata->>'description'), '')) IS NOT NULL"
        )
    elif normalized_completeness == "has_images":
        image_sources = [_core_photo_exists_sql()]
        if fungip_available:
            image_sources.append(_fungip_photo_exists_sql())
        where_clauses.append(_has_images_candidate_sql(fungip_available=fungip_available))
        where_clauses.append("(" + " OR ".join(image_sources) + ")")

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    order_normalized = (order or "").strip().lower()

    if order_by_normalized == "name-asc":
        order_by_normalized, order_normalized = "canonical_name", "asc"
    elif order_by_normalized == "name-desc":
        order_by_normalized, order_normalized = "canonical_name", "desc"
    elif order_by_normalized == "observations":
        order_by_normalized, order_normalized = "observations_count", "desc"
    elif order_by_normalized == "family-asc":
        order_by_normalized, order_normalized = "family", "asc"
    elif order_by_normalized == "featured":
        order_normalized = "asc"
    elif order_by_normalized == "server":
        order_by_normalized, order_normalized = "canonical_name", "asc"

    if order_by_normalized not in {"canonical_name", "observations_count", "obs_count", "family", "featured"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported order_by. Allowed: canonical_name, observations_count, family, featured.",
        )
    if order_normalized not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid order. Allowed: asc, desc.",
        )

    if order_by_normalized == "canonical_name":
        order_expr = "canonical_name"
    elif order_by_normalized == "family":
        order_expr = family_value_sql
    elif order_by_normalized == "featured":
        order_expr = f"CASE WHEN {_OBSERVATION_COUNT_SQL} > 5000 THEN 0 ELSE 1 END"
    else:
        # Prefer bio.taxon_full.obs_count; fall back to metadata for legacy rows.
        order_expr = (
            "COALESCE(obs_count, "
            "CASE "
            "WHEN (metadata->>'observations_count') ~ '^[0-9]+$' "
            "THEN (metadata->>'observations_count')::int "
            "ELSE 0 END)"
        )

    fallback_order = (
        "canonical_name"
        if order_by_normalized == "canonical_name"
        else "obs_count"
    )
    rich_fallback_from = """(
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
                    (SELECT COUNT(*)::bigint FROM obs.observation o WHERE o.taxon_id = t.id) AS obs_count,
                    (SELECT COUNT(*)::bigint FROM media.image i WHERE i.taxon_id = t.id) AS image_count,
                    (SELECT COUNT(*)::bigint FROM media.video v WHERE v.taxon_id = t.id) AS video_count,
                    (SELECT COUNT(*)::bigint FROM media.audio a WHERE a.taxon_id = t.id) AS audio_count,
                    (SELECT COUNT(*)::bigint FROM bio.genome g WHERE g.taxon_id = t.id) AS genome_count,
                    (SELECT COUNT(*)::bigint FROM bio.taxon_compound tc WHERE tc.taxon_id = t.id) AS compound_link_count,
                    (SELECT COUNT(*)::bigint
                     FROM bio.taxon_interaction ti
                     WHERE ti.source_taxon_id = t.id OR ti.target_taxon_id = t.id) AS interaction_count,
                    (SELECT COUNT(*)::bigint FROM bio.publication_taxon pt WHERE pt.taxon_id = t.id) AS publication_count,
                    (SELECT COUNT(*)::bigint FROM bio.taxon_characteristic c WHERE c.taxon_id = t.id) AS characteristic_count
                FROM core.taxon t
            ) AS taxon_list"""
    minimal_fallback_from = """(
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
                    (SELECT COUNT(*)::bigint FROM obs.observation o WHERE o.taxon_id = t.id) AS obs_count,
                    0::bigint AS image_count,
                    0::bigint AS video_count,
                    0::bigint AS audio_count,
                    0::bigint AS genome_count,
                    0::bigint AS compound_link_count,
                    0::bigint AS interaction_count,
                    0::bigint AS publication_count,
                    0::bigint AS characteristic_count
                FROM core.taxon t
            ) AS taxon_list"""

    rows: list[dict[str, Any]] = []
    total = 0
    count_cache_state = "fresh_query"
    category_evidence_fallback = False
    try:
        photo_sql = _core_photo_exists_sql()
        if fungip_available:
            photo_sql = f"({_core_photo_exists_sql()} OR {_fungip_photo_exists_sql()})"
        photo_tiebreak = f"CASE WHEN {photo_sql} THEN 1 ELSE 0 END"
        by_observations = order_by_normalized in {"observations_count", "obs_count"}
        rows, total, count_cache_state = await _list_taxa_core_page(
            db,
            where_sql=where_sql,
            params=params,
            by_popularity=order_by_normalized not in {"canonical_name", "family", "featured"},
            order_normalized=order_normalized,
            order_expr_override=order_expr if order_by_normalized in {"family", "featured"} else None,
            order_source_sql=family_join,
            secondary_order_expr=photo_tiebreak if by_observations else None,
            popularity_head_sql=(
                _popularity_head_sql(photo_sql=photo_sql, fungip_available=fungip_available)
                if by_observations else None
            ),
            include_category_evidence=normalized_category not in {"", "all"},
        )
    except Exception:
        await db.rollback()
        for from_clause in ("bio.taxon_full", rich_fallback_from, minimal_fallback_from):
            try:
                rows, total, count_cache_state = await _list_taxa_query(
                    db,
                    from_clause=from_clause,
                    where_sql=where_sql,
                    params=params,
                    order_expr=order_expr if from_clause == "bio.taxon_full" else fallback_order,
                    order_normalized=order_normalized,
                )
                category_evidence_fallback = normalized_category not in {"", "all"}
                break
            except Exception:
                await db.rollback()
                continue
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Taxonomy list unavailable (core.taxon query failed).",
            )

    taxon_uuids = []
    for row in rows:
        try:
            taxon_uuids.append(UUID(str(row["id"])))
        except (KeyError, TypeError, ValueError):
            continue
    public_members, fungip_index = await load_public_fungip_members(db, taxon_uuids)
    # Identifier lookup and page enrichment are separate optional reads. A
    # successful empty-page enrichment must not erase a failed/unavailable
    # identifier search that may have omitted canonical rows before paging.
    if fungip_search_status is not None:
        if fungip_search_status.status == "error":
            fungip_index = fungip_search_status
        elif fungip_search_status.status == "unavailable" and fungip_index.status == "available":
            fungip_index = fungip_search_status
    if rows and needs_fungip_fields and fungip_available and fungip_index.status != "available":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Source-dependent taxon page unavailable; FungiP enrichment could not verify returned family or image evidence.",
        )
    for row in rows:
        member = public_members.get(str(row.get("id")))
        _project_family(row, member)
        row["image_selection"] = _project_image_selection(row, member)
        row["fungip"] = member

    filter_sources: dict[str, str] = {}
    if normalized_family or order_by_normalized == "family":
        filter_sources["family"] = (
            "resolved family: core.taxon.metadata.family, else first exact validated fungip.species taxonomy.family by species_id, else Unknown; family_evidence preserves both sources"
            if fungip_available else "core.taxon.metadata.family"
        )
    if normalized_category and normalized_category != "all":
        filter_sources["category"] = (
            "core.taxon.metadata.edibility/characteristics + source-qualified bio.taxon_trait "
            "and bio.taxon_characteristic"
        )
    if normalized_completeness == "has_images":
        filter_sources["has_images"] = (
            "first usable photo: exact validated FungiP image, then core default medium/url, then first core photo; URL and credit stay paired"
            if fungip_available else "core.taxon.metadata.default_photo/photos"
        )
    if order_by_normalized in {"observations_count", "obs_count"}:
        filter_sources["observation_tiebreak_photo"] = (
            "usable photo presence: exact validated FungiP image or core default medium/url or first core photo"
            if fungip_available else "core.taxon.metadata.default_photo/photos"
        )
    if normalized_completeness == "has_description":
        filter_sources["has_description"] = "core.taxon.description/metadata.description"

    query_partial_reasons = []
    if needs_fungip_fields and not fungip_available:
        query_partial_reasons.append(
            "FungiP source family/image evidence unavailable; source-dependent filter or photo tie-break "
            "uses core.taxon metadata only"
        )
    if q and fungip_search_status is not None and fungip_search_status.status in {"unavailable", "error"}:
        query_partial_reasons.append(
            "FungiP identifier search unavailable; query covers canonical/common taxon names only"
        )
    if rows and needs_fungip_fields and fungip_index.status != "available":
        query_partial_reasons.append(
            "FungiP enrichment unavailable after native filtering; source-dependent family or image evidence may be omitted"
        )
    if category_evidence_fallback:
        query_partial_reasons.append(
            "Per-row category evidence projection unavailable after the native filtered page query"
        )
    # A cached zero alone can be stale, but an empty first page is a fresh read of the same filter.
    page_proves_empty = not rows and (count_cache_state == "fresh_query" or pagination.offset == 0)
    query_state = "partial" if query_partial_reasons else (
        "empty" if total == 0 and page_proves_empty else "available"
    )

    return TaxonListResponse(
        data=rows,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        },
        fungip_index=fungip_index,
        query={
            "contract_version": "mycosoft.mindex.ancestry.filtered-catalog.v2",
            "status": query_state,
            "count_scope": "matching_core_taxa",
            "count_consistency": "best_effort_not_atomic",
            "count_cache_state": count_cache_state,
            "count_cache_ttl_seconds": _COUNT_CACHE_TTL_SECONDS,
            "filter_sources": filter_sources,
            "partial_reasons": query_partial_reasons,
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
    read_only: bool = False,
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
    public_members, fungip_index = await load_public_fungip_members(db, [taxon_id])
    data["fungip"] = public_members.get(str(data["id"]))
    data["fungip_index"] = fungip_index
    # Read-only detail still includes optional FungiP data, but schedules no enrichment.
    if not read_only and data.get("rank") == "species":
        background_tasks.add_task(_queue_incomplete_taxon, str(taxon_id), data)
    return TaxonResponse(**data)
