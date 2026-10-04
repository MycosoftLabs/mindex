"""Stage source-attested GenBank references as reviewable evidence candidates."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Callable

from ..sources import genbank
from ..sources.publication_evidence import parse_genbank_publication_evidence


def _row_value(row: Any, key: str, index: int):
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _exact_taxon_crosswalk(cur, source_taxon_id: str) -> tuple[str | None, dict]:
    cur.execute(
        """
        SELECT DISTINCT x.taxon_id, t.canonical_name, t.rank
        FROM core.taxon_external_id AS x
        JOIN core.taxon AS t ON t.id = x.taxon_id
        WHERE x.source = %s AND x.external_id = %s
        LIMIT 3
        """,
        ("ncbi", source_taxon_id),
    )
    by_taxon = {}
    for row in cur.fetchall():
        taxon_id = _row_value(row, "taxon_id", 0)
        if taxon_id is not None:
            by_taxon[str(taxon_id)] = {
                "canonical_name": _row_value(row, "canonical_name", 1),
                "rank": _row_value(row, "rank", 2),
            }
    if len(by_taxon) == 0:
        return None, {"state": "unlinked_exact_external_id", "source": "ncbi", "source_ids": [source_taxon_id]}
    if len(by_taxon) > 1:
        return None, {
            "state": "ambiguous_exact_external_id", "source": "ncbi", "source_ids": [source_taxon_id],
            "candidate_count": len(by_taxon),
        }
    taxon_id, identity = next(iter(by_taxon.items()))
    return taxon_id, {"state": "unique", "canonical_name": identity["canonical_name"], "rank": identity["rank"]}


def stage_genbank_publication_evidence(
    conn,
    xml_content: str | bytes,
    *,
    expected_accession_version: str | None = None,
) -> dict:
    """Write publication and provenance candidates inside the caller's transaction.

    The operation never creates rows in ``bio.publication_taxon``. Reviewers may
    promote accepted evidence in a separately reviewed transaction. This makes
    text-search results and source-attested candidates distinct from stored links.
    """
    candidates = parse_genbank_publication_evidence(xml_content)
    if not candidates:
        return {"state": "no_citable_reference", "staged": 0}
    accession_version = candidates[0]["accession_version"]
    if expected_accession_version and accession_version != expected_accession_version:
        return {
            "state": "accession_version_mismatch", "requested": expected_accession_version,
            "returned": accession_version, "staged": 0,
        }
    source_taxon_ids = sorted({taxon_id for c in candidates for taxon_id in c["source_taxon_ids"]})
    if len(source_taxon_ids) != 1:
        state = "source_taxon_id_missing" if not source_taxon_ids else "source_taxon_id_ambiguous_or_invalid"
        return {"state": state, "source_taxon_ids": source_taxon_ids, "staged": 0}

    with conn.cursor() as cur:
        taxon_id, identity = _exact_taxon_crosswalk(cur, source_taxon_ids[0])
        if not taxon_id:
            return {**identity, "staged": 0}
        source_name = " ".join((candidates[0].get("organism") or "").split())
        canonical_name = " ".join((identity.get("canonical_name") or "").split())
        if not source_name:
            return {
                "state": "source_name_missing_unverified", "source": "ncbi",
                "source_ids": source_taxon_ids, "staged": 0,
            }
        if source_name.casefold() != canonical_name.casefold():
            return {
                "state": "source_name_mismatch", "source": "ncbi", "source_ids": source_taxon_ids,
                "source_name": source_name, "canonical_name": canonical_name, "staged": 0,
            }

        staged = 0
        for candidate in candidates:
            provider = "ncbi_genbank_reference"
            external_id = (
                f"{candidate['provider_source_record_id']}@sha256:{candidate['source_content_sha256'][:16]}"
            )
            publication_id = hashlib.sha256(f"{provider}:{external_id}".encode("utf-8")).hexdigest()[:32]
            publication_metadata = {
                "evidence_state": "candidate_source_attested",
                "provider_taxon_id": source_taxon_ids[0],
                "provider_source_record_id": external_id,
                "source_content_sha256": candidate["source_content_sha256"],
                "normalization_sha256": candidate["normalization_sha256"],
                "journal": candidate.get("journal"),
                "pubmed_id": candidate.get("pubmed_id"),
            }
            cur.execute(
                """
                INSERT INTO core.publications (
                    id, source, external_id, title, authors, year, abstract, url, doi, metadata,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s::jsonb, NULL, NULL, %s, %s, %s::jsonb, now(), now())
                ON CONFLICT (source, external_id) DO NOTHING
                """,
                (
                    publication_id, provider, external_id, candidate["title"],
                    json.dumps(candidate.get("authors") or []), candidate["provider_source_url"],
                    candidate.get("doi"), json.dumps(publication_metadata, sort_keys=True),
                ),
            )
            cur.execute(
                "SELECT id FROM core.publications WHERE source = %s AND external_id = %s",
                (provider, external_id),
            )
            publication_row = cur.fetchone()
            resolved_publication_id = _row_value(publication_row, "id", 0)
            if resolved_publication_id is None:
                raise RuntimeError("publication row was not available after idempotent insert")
            evidence_metadata = {
                "organism": source_name,
                "canonical_name": canonical_name,
                "canonical_rank": identity.get("rank"),
                "reference_number": candidate["reference_number"],
                "title": candidate["title"],
                "journal": candidate.get("journal"),
                "pubmed_id": candidate.get("pubmed_id"),
                "doi": candidate.get("doi"),
            }
            cur.execute(
                """
                INSERT INTO bio.publication_taxon_evidence (
                    publication_id, taxon_id, provider, provider_taxon_id,
                    provider_source_record_id, provider_source_url, association_method,
                    evidence_state, source_content_sha256, normalization_sha256,
                    metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (provider, provider_source_record_id, source_content_sha256, taxon_id)
                DO NOTHING
                """,
                (
                    resolved_publication_id, taxon_id, candidate["provider"], source_taxon_ids[0],
                    candidate["provider_source_record_id"], candidate["provider_source_url"], candidate["association_method"],
                    candidate["evidence_state"], candidate["source_content_sha256"],
                    candidate["normalization_sha256"], json.dumps(evidence_metadata, sort_keys=True),
                ),
            )
            staged += 1
    return {
        "state": "candidate_source_attested",
        "accession_version": accession_version,
        "source_taxon_id": source_taxon_ids[0],
        "taxon_id": taxon_id,
        "staged": staged,
    }


def fetch_and_stage_genbank_publication_evidence(
    conn,
    accession_version: str,
    *,
    expected_source_sha256: str,
    fetcher: Callable[[str], str] = genbank.fetch_accession_xml,
) -> dict:
    """Fetch one exact accession.version, then stage only its source citations."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256 or ""):
        raise ValueError("expected_source_sha256 must be a 64-character lowercase SHA-256 pin")
    xml_content = fetcher(accession_version)
    candidates = parse_genbank_publication_evidence(xml_content)
    if not candidates:
        return {"state": "no_citable_reference", "staged": 0}
    actual_hash = candidates[0]["source_content_sha256"]
    if actual_hash != expected_source_sha256:
        return {
            "state": "source_hash_mismatch", "expected": expected_source_sha256,
            "actual": actual_hash, "staged": 0,
        }
    return stage_genbank_publication_evidence(
        conn, xml_content, expected_accession_version=accession_version,
    )
