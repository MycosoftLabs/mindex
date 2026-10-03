"""Captured FungiP references only; no provider imports or implicit connection.

Draft destination: mindex_etl/fungip/genetic_references.py. Default CLI is offline.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import UUID

from .catalog import canonical, digest, its_span, resolve, source_ids


CATALOG_SHA = "b33d08f061d8cbd1cfdda0ca0ace6b174ada2d3cfba84f3e04b1980f5bf82a9a"
PREFLIGHT_SHA = "6f25ccf1757155ce891832b5e6ccde608ea95a01f51d613deb42370a409e13d8"
PROJECTION_SHA = "9d0a68c14ef3e79fb234b72a0fec8cbd4619d40e26db20af8dee853dcd571dc2"
TARGET = {"host": "127.0.0.1", "port": "5547", "dbname": "fungip_native_20261002"}
SCHEMA = "fungip.captured_reference.v1"
MAX_RECORDS = 300
SEMANTIC_FIELDS = (
    "accession", "version", "taxon_id", "species_name", "gene", "region",
    "sequence", "sequence_length", "sequence_type", "source", "source_url",
    "definition", "organism", "taxonomy", "metadata",
)


class Rejected(ValueError):
    """Only fixed reason codes, never driver messages or connection details."""


class CommitUnknown(RuntimeError):
    """Commit acknowledgement failed; never report a confirmed rollback."""


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def read_bound_json(path, expected):
    raw = Path(path).read_bytes()
    require(digest(raw) == expected, "input_hash_mismatch")
    return json.loads(raw)


def semantic_row(capture):
    dna = capture["original_dna"]
    products = [p for f in dna.get("its_features", []) for p in f.get("qualifiers", {}).get("product", [])]
    return {
        "accession": dna["accession_version"], "version": dna["accession_version"],
        "taxon_id": capture["candidate_taxon_id"], "species_name": dna["organism"],
        "gene": "ITS" if any(p in ("internal transcribed spacer 1", "internal transcribed spacer 2")
                             for p in products) else None,
        "region": None, "sequence": dna["sequence"], "sequence_length": len(dna["sequence"]),
        "sequence_type": "dna", "source": {"RefSeq": "refseq", "GenBank": "genbank"}[dna["database"]],
        "source_url": dna["source_url"], "definition": dna.get("definition"), "organism": dna["organism"],
        "taxonomy": dna.get("ncbi_taxonomy_lineage"), "metadata": {"fungip_capture": capture},
    }


def project(catalog, preflight, package):
    """Pure 300-record projection. UUIDs remain provisional until DB revalidation."""
    records = catalog.get("species", [])
    proofs = preflight.get("records", [])
    expected = {f"FG{i:03d}" for i in range(1, 301)}
    require(len(records) == len(proofs) == MAX_RECORDS, "record_limit_or_count")
    require({r.get("species_id") for r in records} == expected, "catalog_identity_set")
    require({r.get("species_id") for r in proofs} == expected, "proof_identity_set")
    proofs = {r["species_id"]: r for r in proofs}
    rows = []
    seen = set()
    for record in sorted(records, key=lambda r: r["species_id"]):
        sid = record["species_id"]
        proof = proofs[sid]
        dna = record.get("dna") or {}
        require(digest(canonical(record).encode("utf-8")) == proof["record_sha256"], "record_binding")
        sidecar = Path(package) / "data" / "dna" / f"{sid}.json"
        raw = sidecar.read_bytes()
        require(digest(raw) == proof["dna_sidecar"]["sha256"] and
                len(raw) == proof["dna_sidecar"]["bytes"] and json.loads(raw) == dna, "dna_sidecar_binding")
        sequence, accession = dna.get("sequence"), dna.get("accession_version")
        require(isinstance(sequence, str) and re.fullmatch(r"[ACGTRYSWKMBDHVN]+", sequence), "sequence_alphabet")
        require(len(sequence) == dna.get("length_bp") == proof["length_bp"] and
                digest(sequence.encode("ascii")) == dna.get("sha256") == proof["sequence_sha256"], "sequence_binding")
        require(isinstance(accession, str) and len(accession) <= 50 and
                re.fullmatch(r"[A-Z]+_?\d+\.\d+", accession) and accession == proof["accession_version"], "accession_binding")
        require(accession not in seen, "duplicate_accession")
        seen.add(accession)
        require(not dna.get("errors") and not proof.get("errors"), "source_errors")
        require(dna.get("species_universal_genome") is False, "unsupported_genome_claim")
        require(dna.get("organism") == proof["source_organism"] and
                record["accepted_name"] == proof["accepted_name"], "organism_binding")
        evidence = proof.get("crosswalk_evidence", [])
        saved_uuid = proof.get("saved_canonical_uuid")
        resolved_uuid = proof.get("independently_recomputed_crosswalk_uuid")
        candidate = None
        if (saved_uuid and saved_uuid == resolved_uuid and dna["organism"] == record["accepted_name"] and
                evidence and {e["taxon_id"] for e in evidence} == {saved_uuid}):
            candidate = str(UUID(saved_uuid))
        source = {"RefSeq": "refseq", "GenBank": "genbank"}.get(dna.get("database"))
        require(source is not None, "unsupported_reference_source")
        url = dna.get("source_url")
        parsed = urlsplit(url) if isinstance(url, str) else None
        require(parsed and parsed.scheme == "https" and parsed.hostname and
                not parsed.username and not parsed.password, "invalid_source_url")
        supported_its = its_span(dna)
        capture = {
            "schema": SCHEMA, "species_id": sid, "accepted_name": record["accepted_name"],
            "catalog_sha256": CATALOG_SHA, "record_sha256": proof["record_sha256"],
            "preflight_sha256": PREFLIGHT_SHA, "dna_sidecar_sha256": digest(raw),
            "sequence_sha256": dna["sha256"], "original_dna": copy.deepcopy(dna),
            "external_ids": source_ids(record), "saved_canonical_uuid": saved_uuid,
            "candidate_taxon_id": candidate,
            "link_policy": "strict_current_resolver_and_kingdom" if candidate else "canonical_pending",
            "complete_its_supported": supported_its is not None,
            "verified_its_sha256": digest(supported_its.encode("ascii")) if supported_its is not None else None,
            "reference_is_whole_species_genome": False,
            "specimen_identity": "reference sequence and image are not established as the same specimen",
        }
        rows.append(semantic_row(capture))
    require(sum(r["taxon_id"] is not None for r in rows) == 246, "canonical_candidate_count")
    require(sum(r["metadata"]["fungip_capture"]["complete_its_supported"] for r in rows) == 42,
            "complete_its_count")
    return {"schema": SCHEMA, "catalog_sha256": CATALOG_SHA, "preflight_sha256": PREFLIGHT_SHA,
            "counts": {"references": 300, "canonical_candidates": 246, "canonical_pending": 54,
                       "exact_complete_its": 42}, "rows": rows}


def load_projection(package, preflight):
    package = Path(package)
    catalog = read_bound_json(package / "data" / "catalog.json", CATALOG_SHA)
    proof = read_bound_json(preflight, PREFLIGHT_SHA)
    projection = project(catalog, proof, package)
    require(digest(canonical(projection).encode("utf-8")) == PROJECTION_SHA, "projection_content_changed")
    return projection


def validate_target(parameters):
    allowed = {"host", "port", "dbname", "user", "password", "sslmode", "connect_timeout", "application_name"}
    require(set(parameters) <= allowed, "connection_option_not_allowed")
    require(all(str(parameters.get(k, "")) == v for k, v in TARGET.items()), "wrong_database_target")


def validate_connected_target(target):
    try:
        address = ipaddress.ip_interface(target["address"]).ip
    except (ValueError, TypeError, KeyError):
        raise Rejected("connected_target_differs") from None
    require(target["db"] == TARGET["dbname"] and target["port"] == 5547 and
            address == ipaddress.ip_address(TARGET["host"]), "connected_target_differs")


@contextmanager
def import_transaction(conn):
    body_completed = False
    try:
        with conn.transaction():
            yield
            body_completed = True
    except Exception:
        if body_completed:
            raise CommitUnknown("commit_outcome_unknown") from None
        raise


def same_semantics(existing, proposed):
    for field in SEMANTIC_FIELDS:
        actual, wanted = existing.get(field), proposed.get(field)
        if field == "taxon_id":
            actual = str(actual) if actual is not None else None
        if field == "metadata" and isinstance(actual, str):
            actual = json.loads(actual)
        if actual != wanted:
            return False
    return True


def validate_source(proposed, stored, candidates):
    capture = proposed["metadata"]["fungip_capture"]
    require(capture["schema"] == SCHEMA and capture["catalog_sha256"] == CATALOG_SHA and
            capture["preflight_sha256"] == PREFLIGHT_SHA and same_semantics(proposed, semantic_row(capture)),
            "proposed_semantics_changed")
    require(stored is not None, "stored_species_missing")
    record = stored["record"] if isinstance(stored["record"], dict) else json.loads(stored["record"])
    external = stored["external_ids"] if isinstance(stored["external_ids"], list) else json.loads(stored["external_ids"])
    require(stored["record_sha256"] == capture["record_sha256"] and
            digest(canonical(record).encode("utf-8")) == capture["record_sha256"] and
            stored["catalog_sha256"] == CATALOG_SHA and record.get("dna") == capture["original_dna"], "stored_source_changed")
    require(stored["sequence_valid"] is True and external == capture["external_ids"], "stored_validation_changed")
    errors = stored["validation_errors"]
    require((json.loads(errors) if isinstance(errors, str) else errors) == [], "stored_validation_errors")
    actual_uuid = str(stored["taxon_id"]) if stored["taxon_id"] else None
    require(actual_uuid == capture["saved_canonical_uuid"], "stored_mapping_changed")
    if proposed["taxon_id"] is None:
        return
    relevant = [dict(c, taxon_id=str(c["taxon_id"])) for c in candidates
                if (c["source"], str(c["external_id"])) in
                {(i["source"], i["external_id"]) for i in external}]
    uuid, state = resolve({"record": record, "external_ids": external}, relevant)
    require(state == stored["resolution_status"] == "resolved" and uuid == actual_uuid == proposed["taxon_id"],
            "canonical_resolution_changed")
    require(relevant and all(c["kingdom"] == "Fungi" for c in relevant) and
            record["accepted_name"] == proposed["organism"], "canonical_classification_not_qualified")


def import_projection(conn, projection, *, deadline_seconds=120):
    """Only the sole native owner may call this; all changes share one transaction."""
    validate_target({"host": conn.info.host, "port": str(conn.info.port), "dbname": conn.info.dbname})
    require(digest(canonical(projection).encode("utf-8")) == PROJECTION_SHA, "projection_content_changed")
    rows = projection["rows"]
    require(projection["schema"] == SCHEMA and projection["catalog_sha256"] == CATALOG_SHA and
            projection["preflight_sha256"] == PREFLIGHT_SHA and len(rows) == MAX_RECORDS and
            len({r["accession"] for r in rows}) == MAX_RECORDS, "projection_binding")
    require(sum(r["taxon_id"] is not None for r in rows) == 246 and
            {r["metadata"]["fungip_capture"]["species_id"] for r in rows} ==
            {f"FG{i:03d}" for i in range(1, 301)}, "projection_identity_set")
    require(0 < deadline_seconds <= 120, "deadline_not_bounded")
    started = time.monotonic()

    def execute(sql, parameters=None):
        require(time.monotonic() - started < deadline_seconds, "import_deadline")
        return conn.execute(sql, parameters)

    inserted = unchanged = 0
    with import_transaction(conn):
        execute("SET LOCAL statement_timeout = '2000ms'")
        execute("SET LOCAL lock_timeout = '500ms'")
        execute("SET LOCAL search_path = pg_catalog")
        execute("SELECT pg_advisory_xact_lock(hashtext('fungip.captured_reference.import'))")
        target = execute("SELECT current_database() AS db, inet_server_addr()::text AS address, inet_server_port() AS port").fetchone()
        validate_connected_target(target)
        ids = [r["metadata"]["fungip_capture"]["species_id"] for r in rows]
        sources = execute("SELECT * FROM fungip.species WHERE species_id = ANY(%s) ORDER BY species_id FOR SHARE", (ids,)).fetchall()
        require(len(sources) == MAX_RECORDS and {s["species_id"] for s in sources} == set(ids), "stored_species_set")
        sources = {s["species_id"]: s for s in sources}
        keys = sorted({(i["source"], i["external_id"]) for r in rows
                       for i in r["metadata"]["fungip_capture"]["external_ids"]})
        candidates = execute("""SELECT e.source,e.external_id,e.taxon_id,t.canonical_name,t.rank,t.kingdom
            FROM core.taxon_external_id e
            JOIN jsonb_to_recordset(%s::jsonb) AS q(source text,external_id text)
              ON q.source=e.source AND q.external_id=e.external_id
            JOIN core.taxon t ON t.id=e.taxon_id FOR SHARE OF e,t""",
            (canonical([{"source":s,"external_id":i} for s,i in keys]),)).fetchall()
        for row in rows:
            validate_source(row, sources[row["metadata"]["fungip_capture"]["species_id"]], candidates)
        accessions = [r["accession"] for r in rows]
        aliases = sorted(set(accessions + [a.rsplit(".", 1)[0] for a in accessions]))
        existing = execute("SELECT * FROM bio.genetic_sequence WHERE accession = ANY(%s) OR version = ANY(%s) FOR UPDATE",
                           (aliases, accessions)).fetchall()
        by_accession = {r["accession"]: r for r in existing}
        for row in rows:
            collisions = [r for r in existing if r["accession"] in (row["accession"], row["accession"].rsplit(".", 1)[0])
                          or r.get("version") == row["accession"]]
            require(not collisions or (len(collisions) == 1 and same_semantics(collisions[0], row)), "accession_conflict")
        # Source and all existing accession conflicts are checked before the first insert.
        fields = ",".join(SEMANTIC_FIELDS)
        placeholders = ",".join(["%s"] * (len(SEMANTIC_FIELDS) - 1) + ["%s::jsonb"])
        for row in rows:
            if row["accession"] in by_accession:
                unchanged += 1
                continue
            values = tuple(canonical(row[k]) if k == "metadata" else row[k] for k in SEMANTIC_FIELDS)
            result = execute(f"INSERT INTO bio.genetic_sequence ({fields}) VALUES ({placeholders}) ON CONFLICT (accession) DO NOTHING RETURNING id", values)
            if result.fetchone() is not None:
                inserted += 1
            else:
                concurrent = execute("SELECT * FROM bio.genetic_sequence WHERE accession=%s FOR UPDATE", (row["accession"],)).fetchone()
                require(concurrent is not None and same_semantics(concurrent, row), "concurrent_accession_conflict")
                unchanged += 1
    return {"status": "committed", "schema": SCHEMA, "catalog_sha256": CATALOG_SHA,
            "preflight_sha256": PREFLIGHT_SHA, "inserted": inserted, "unchanged": unchanged,
            "updated": 0, "references": MAX_RECORDS, "canonical_linked": 246, "canonical_pending": 54}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Captured FungiP DNA projection; offline by default")
    parser.add_argument("package", type=Path)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    # Reserve a new receipt before any connection; never overwrite an earlier result.
    with args.output.open("x", encoding="utf-8") as output:
        report = None
        try:
            projection = load_projection(args.package, args.preflight)
            if not args.apply:
                report = {"status": "offline_projection", **projection}
            else:
                import psycopg
                from psycopg.conninfo import conninfo_to_dict
                from psycopg.rows import dict_row
                parameters = conninfo_to_dict(os.environ.get("FUNGIP_IMPORT_DSN", ""))
                validate_target(parameters)
                parameters.update(connect_timeout=3, application_name="fungip_captured_reference")
                with psycopg.connect(**parameters, autocommit=True, row_factory=dict_row) as conn:
                    report = import_projection(conn, projection)
        except CommitUnknown:
            report = {"status": "commit_outcome_unknown", "inserted": None, "updated": None,
                      "retry_requires_readback": True}
        except Rejected as exc:
            report = {"status": "rejected", "reason": str(exc), "inserted": 0, "updated": 0}
        except Exception:
            if report and report.get("status") == "committed":
                report["connection_cleanup_failed"] = True
            else:
                report = {"status": "rejected", "reason": "operation_failed", "inserted": 0, "updated": 0}
        try:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.flush()
        except Exception:
            print(json.dumps({"status": report["status"], "receipt_write_failed": True,
                              "inserted": report.get("inserted", 0)}))
            return 3
    print(json.dumps({k:v for k,v in report.items() if k != "rows"}))
    return 0 if report["status"] in ("offline_projection", "committed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
