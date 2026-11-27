"""Import Davidson 2026 magnetoreception list into MINDEX.

Source: Davidson, B. (2026). "Largest List of Life Known to Use or Respond to
Magnetic Fields", Zenodo, v2, https://doi.org/10.5281/zenodo.24571664
License: CC BY 4.0 (attribution required; kept in every row's metadata).

What it writes (existing tables only, no DDL, no deletes, idempotent):
  core.publications           one source record (source='zenodo', external_id='24571664')
  core.taxon                  ONLY for named species not already in MINDEX
  core.taxon_external_id      GBIF usage key for newly created taxa (ON CONFLICT DO NOTHING)
  bio.taxon_trait             'magnetic_field_response' (use_class) and
                              'magnetic_evidence_status' (Confirmed/Strong/Suspected)
  bio.publication_taxon       taxon <-> source publication link

Linking order: existing GBIF crosswalk (core.taxon_external_id source='gbif')
-> unique case-insensitive canonical_name + kingdom -> create (named species only).
Existing taxa are never updated. Genus-level / group / review rows that cannot be
linked are kept in the publication metadata as unlinked entries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "magnetoreception" / "davidson2026"
ENTRIES_FILE = DATA_DIR / "davidson2026_magnetoreception_entries.json"
SOURCE_CSV = DATA_DIR / "MASTER-species.csv"
SOURCE_CSV_SHA256 = "7a4e94b818d68270bf118d7d4a49ee4dae9b12a5f3f940223848cb2f2ed6cd79"

ENTRIES_SHA256 = "320a809770ae2ab0526b6b9f350d7f1262c1250ccdcde0164909e69f9885df39"
IMPORT_LOCK = 24571664

SOURCE_TAG = "davidson2026_zenodo_24571664"
PUBLICATION_ID = "zenodo:24571664"
ATTRIBUTION = (
    "Davidson, B. (2026). Largest List of Life Known to Use or Respond to Magnetic Fields "
    "(v2). Space Weather News, LLC. Zenodo. https://doi.org/10.5281/zenodo.24571664 - CC BY 4.0"
)
TRAIT_FIELDS = (
    "entry_id",
    "source_row",
    "scientific_name",
    "common_name",
    "kingdom_source",
    "taxon_group",
    "use_class",
    "magnetic_response_types",
    "status",
    "weak_field_effect",
    "applied_field_effect",
    "mechanism_keywords",
    "one_line_finding",
    "citation",
    "doi_or_url",
    "year",
    "flags",
    "gbif_usage_key",
    "gbif_match_type",
)


def load_entries(
    path: Path = ENTRIES_FILE, *, verify_source: bool = True
) -> tuple[dict, list[dict]]:
    if verify_source:
        if not SOURCE_CSV.is_file():
            raise ValueError("Required source CSV is missing")
        if hashlib.sha256(path.read_bytes()).hexdigest() != ENTRIES_SHA256:
            raise ValueError("Cleaned entries sha256 mismatch")
        digest = hashlib.sha256(SOURCE_CSV.read_bytes()).hexdigest()
        if digest != SOURCE_CSV_SHA256:
            raise ValueError(f"MASTER-species.csv sha256 mismatch: {digest}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc["source"], doc["entries"]


def trait_metadata(entry: dict) -> dict[str, Any]:
    meta = {k: entry.get(k) for k in TRAIT_FIELDS}
    meta.update(
        {
            "dataset_doi": "10.5281/zenodo.24571664",
            "license": "CC-BY-4.0",
            "attribution": ATTRIBUTION,
            "evidence_basis": "author_compilation_not_independently_validated",
            "classification_origin": "source_reported",
        }
    )
    return meta


def importable(entry: dict) -> bool:
    return not entry.get("is_review_row") and entry.get("kingdom_mindex") is not None


def _ids(cur) -> set[str]:
    return {str(r["taxon_id"] if "taxon_id" in r else r["id"]) for r in cur.fetchall()}


def resolve_taxon(cur, entry: dict) -> tuple[str | None, str]:
    key = entry.get("gbif_usage_key")
    if key is not None:
        cur.execute(
            "SELECT DISTINCT taxon_id FROM core.taxon_external_id WHERE source = 'gbif' AND external_id = %s LIMIT 3",
            (str(key),),
        )
        found = _ids(cur)
        if len(found) == 1:
            return found.pop(), "gbif_crosswalk"
    cur.execute(
        "SELECT id FROM core.taxon WHERE lower(canonical_name) = lower(%s) AND (kingdom = %s OR kingdom IS NULL) LIMIT 3",
        (entry["scientific_name"], entry["kingdom_mindex"]),
    )
    found = _ids(cur)
    if len(found) == 1:
        return found.pop(), "name_match"
    if len(found) > 1:
        return (
            None,
            "ambiguous",
        )  # never create a duplicate when MINDEX already has several candidates
    return None, "unresolved"


def create_taxon(cur, entry: dict) -> str:
    cur.execute(
        """
        INSERT INTO core.taxon (canonical_name, rank, common_name, source, kingdom, metadata)
        VALUES (%s, 'species', %s, %s, %s, %s::jsonb) RETURNING id
        """,
        (
            entry["scientific_name"],
            entry.get("common_name"),
            SOURCE_TAG,
            entry["kingdom_mindex"],
            json.dumps({"created_by_import": SOURCE_TAG, "attribution": ATTRIBUTION}),
        ),
    )
    taxon_id = str(cur.fetchone()["id"])
    if entry.get("gbif_usage_key") is not None:
        cur.execute(
            """
            INSERT INTO core.taxon_external_id (taxon_id, source, external_id, metadata)
            VALUES (%s, 'gbif', %s, %s::jsonb) ON CONFLICT (source, external_id) DO NOTHING
            """,
            (
                taxon_id,
                str(entry["gbif_usage_key"]),
                json.dumps({"via": SOURCE_TAG, "match": "EXACT"}),
            ),
        )
    return taxon_id


def add_trait(cur, taxon_id: str, name: str, value: str, meta: dict) -> int:
    cur.execute(
        """
        INSERT INTO bio.taxon_trait (taxon_id, trait_name, value_text, source, metadata)
        SELECT %s, %s, %s, %s, %s::jsonb
        WHERE NOT EXISTS (
            SELECT 1 FROM bio.taxon_trait WHERE taxon_id = %s AND trait_name = %s AND source = %s
        )
        """,
        (taxon_id, name, value, SOURCE_TAG, json.dumps(meta), taxon_id, name, SOURCE_TAG),
    )
    return cur.rowcount or 0


def upsert_publication(cur, source: dict, summary: dict) -> str:
    cur.execute(
        """
        INSERT INTO core.publications (id, source, external_id, title, authors, year, url, doi, metadata)
        VALUES (%s, 'zenodo', '24571664', %s, %s::jsonb, 2026, %s, %s, %s::jsonb)
        ON CONFLICT (source, external_id) DO UPDATE SET metadata = EXCLUDED.metadata, updated_at = now()
        RETURNING id
        """,
        (
            PUBLICATION_ID,
            source["title"],
            json.dumps([source["creator"]]),
            source["url"],
            source["doi"],
            json.dumps(
                {
                    **summary,
                    "license": "CC-BY-4.0",
                    "attribution": ATTRIBUTION,
                    "version": source.get("version"),
                    "source_csv_sha256": SOURCE_CSV_SHA256,
                    "import_source_tag": SOURCE_TAG,
                }
            ),
        ),
    )

    return str(cur.fetchone()["id"])


def run(conn, entries: list[dict], source: dict, *, create_missing: bool = True) -> dict:
    stats = {
        "entries": len(entries),
        "linked_gbif_crosswalk": 0,
        "linked_name_match": 0,
        "taxa_created": 0,
        "traits_inserted": 0,
        "unlinked": 0,
    }
    unlinked: list[dict] = []
    links: list[str] = []
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s) AS locked", (IMPORT_LOCK,))
        if not cur.fetchone()["locked"]:
            raise RuntimeError("This dataset import is already running")
        for entry in entries:
            if not importable(entry):
                unlinked.append(
                    {
                        "entry_id": entry["entry_id"],
                        "name": entry["scientific_name"],
                        "reason": "review_or_mixed_kingdom",
                    }
                )
                continue
            taxon_id, how = resolve_taxon(cur, entry)
            if (
                taxon_id is None
                and how == "unresolved"
                and create_missing
                and entry.get("is_named_species")
            ):
                taxon_id, how = create_taxon(cur, entry), "created"
            if taxon_id is None:
                unlinked.append(
                    {
                        "entry_id": entry["entry_id"],
                        "name": entry["scientific_name"],
                        "reason": (
                            "ambiguous_existing_taxa"
                            if how == "ambiguous"
                            else "not_a_named_species_or_unmatched"
                        ),
                    }
                )
                continue
            stats[
                {
                    "gbif_crosswalk": "linked_gbif_crosswalk",
                    "name_match": "linked_name_match",
                    "created": "taxa_created",
                }[how]
            ] += 1
            meta = trait_metadata(entry) | {"link_method": how}
            stats["traits_inserted"] += add_trait(
                cur, taxon_id, "magnetic_field_response", entry["use_class"], meta
            )
            stats["traits_inserted"] += add_trait(
                cur, taxon_id, "magnetic_evidence_status", entry["status"], meta
            )
            links.append(taxon_id)
        stats["unlinked"] = len(unlinked)
        stats["unlinked_group_labels"] = sum(
            e["reason"] == "not_a_named_species_or_unmatched" for e in unlinked
        )
        stats["unlinked_ambiguous"] = sum(
            e["reason"] == "ambiguous_existing_taxa" for e in unlinked
        )
        stats["unlinked_review_or_mixed"] = sum(
            e["reason"] == "review_or_mixed_kingdom" for e in unlinked
        )
        publication_id = upsert_publication(
            cur, source, {"import_stats": stats, "unlinked_entries": unlinked}
        )
        stats["publication_links_inserted"] = 0
        for taxon_id in sorted(set(links)):
            cur.execute(
                "INSERT INTO bio.publication_taxon (publication_id, taxon_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (publication_id, taxon_id),
            )
            stats["publication_links_inserted"] += cur.rowcount or 0
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Davidson 2026 magnetoreception list (CC BY 4.0) into MINDEX"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run inside a transaction and roll back"
    )
    parser.add_argument(
        "--no-create", action="store_true", help="only link to taxa that already exist"
    )
    args = parser.parse_args()
    from ..db import get_connection

    source, entries = load_entries()
    conn = get_connection()
    try:
        stats = run(conn, entries, source, create_missing=not args.no_create)
        conn.rollback() if args.dry_run else conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(json.dumps({"dry_run": args.dry_run, **stats}, indent=2))


if __name__ == "__main__":
    main()
