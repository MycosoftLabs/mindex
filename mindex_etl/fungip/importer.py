"""Cursor-operated PostgreSQL importer. Importing this module never connects or writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from .catalog import (
    IDENTIFIER_DERIVATION_VERSION, audit, canonical, digest,
    identifier_manifest_sha256, resolve, source_ids,
)


def import_manifest(conn, manifest: dict) -> dict:
    """One transaction, source-qualified resolution, immutable revisions, no core writes."""
    report = {"inserted": 0, "updated": 0, "unchanged": 0, "resolved": 0, "unresolved": 0,
              "conflicts": [], "errors": []}
    if manifest["hash_conflicts"] or any(manifest["duplicates"].values()):
        raise ValueError("Package integrity/duplicate conflicts require review before import")
    derivation = manifest.get("identifier_derivation")
    if derivation is not None:
        if derivation != {"version": IDENTIFIER_DERIVATION_VERSION,
                          "sha256": identifier_manifest_sha256(manifest["records"])}:
            raise ValueError("Identifier derivation binding differs")
        for entry in manifest["records"]:
            if (entry["record_sha256"] != digest(canonical(entry["record"]).encode("utf-8"))
                    or entry["external_ids"] != source_ids(entry["record"])):
                raise ValueError("Derived identifiers differ from captured scientific record")
    with conn.transaction():
        # Serialize collection import; ledger uses its own scope and never this lock.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('fungip.catalog.import'))")
        conn.execute("INSERT INTO fungip.import_run(catalog_sha256,manifest) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING",
                     (manifest["catalog_sha256"], canonical({k:v for k,v in manifest.items() if k != "records"})))
        proposed = []
        for entry in manifest["records"]:
            candidates = []
            for identifier in entry["external_ids"]:
                candidates.extend(conn.execute("""SELECT e.source,e.external_id,e.taxon_id,t.canonical_name,t.rank
                    FROM core.taxon_external_id e JOIN core.taxon t ON t.id=e.taxon_id
                    WHERE e.source=%s AND e.external_id=%s""",
                    (identifier["source"], identifier["external_id"])).fetchall())
            uuid, state = resolve(entry, candidates)
            proposed.append((entry, uuid, state))
        uuid_counts = {}
        for _, uuid, _ in proposed:
            if uuid:
                uuid_counts[uuid] = uuid_counts.get(uuid, 0) + 1
        for entry, uuid, state in proposed:
            sid = entry["species_id"]
            existing = conn.execute("SELECT * FROM fungip.species WHERE species_id=%s FOR UPDATE", (sid,)).fetchone()
            if uuid and (uuid_counts[uuid] > 1 or conn.execute(
                "SELECT species_id FROM fungip.species WHERE taxon_id=%s AND species_id<>%s", (uuid,sid)).fetchone()):
                uuid, state = None, "duplicate_canonical"
            if existing and existing["taxon_id"] and str(existing["taxon_id"]) != uuid:
                # Never silently detach a known taxon or retarget durable token links.
                report["conflicts"].append({"species_id":sid,"reason":"mapping_changed_requires_curation"})
                continue
            if state not in ("resolved", "unresolved"):
                report["conflicts"].append({"species_id":sid,"reason":state})
            report["resolved" if uuid else "unresolved"] += 1
            flags = [f for f in entry["missing_data_flags"] if not uuid or f != "canonical_uuid_unresolved"]
            values = (sid,uuid,entry["record"]["accepted_name"],canonical(entry["record"]),
                      canonical(entry["external_ids"]),entry["verified_its_sequence"],canonical(flags),
                      canonical(entry["errors"]),entry["record_sha256"],manifest["catalog_sha256"],state,entry["image_valid"],entry["sequence_valid"])
            conn.execute("""INSERT INTO fungip.species(species_id,taxon_id,accepted_name,record,external_ids,
              verified_its_sequence,missing_data_flags,validation_errors,record_sha256,catalog_sha256,resolution_status,image_valid,sequence_valid)
              VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)
              ON CONFLICT(species_id) DO UPDATE SET taxon_id=EXCLUDED.taxon_id,accepted_name=EXCLUDED.accepted_name,
              record=EXCLUDED.record,external_ids=EXCLUDED.external_ids,verified_its_sequence=EXCLUDED.verified_its_sequence,
              missing_data_flags=EXCLUDED.missing_data_flags,validation_errors=EXCLUDED.validation_errors,
              record_sha256=EXCLUDED.record_sha256,catalog_sha256=EXCLUDED.catalog_sha256,
              resolution_status=EXCLUDED.resolution_status,image_valid=EXCLUDED.image_valid,sequence_valid=EXCLUDED.sequence_valid,updated_at=now()
              WHERE (fungip.species.record_sha256,fungip.species.taxon_id,fungip.species.resolution_status,
                fungip.species.verified_its_sequence,fungip.species.missing_data_flags,fungip.species.validation_errors,fungip.species.image_valid,fungip.species.sequence_valid,fungip.species.external_ids)
              IS DISTINCT FROM (EXCLUDED.record_sha256,EXCLUDED.taxon_id,EXCLUDED.resolution_status,
                EXCLUDED.verified_its_sequence,EXCLUDED.missing_data_flags,EXCLUDED.validation_errors,EXCLUDED.image_valid,EXCLUDED.sequence_valid,EXCLUDED.external_ids)""", values)
            conn.execute("""INSERT INTO fungip.species_revision(species_id,record_sha256,record,catalog_sha256)
              VALUES (%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING""",
              (sid,entry["record_sha256"],canonical(entry["record"]),manifest["catalog_sha256"]))
            def existing_json(key):
                value = existing[key]
                return json.loads(value) if isinstance(value,str) else value
            unchanged = bool(existing and existing["record_sha256"] == entry["record_sha256"] and
                str(existing["taxon_id"] or "") == (uuid or "") and existing["resolution_status"] == state and
                existing["verified_its_sequence"] == entry["verified_its_sequence"] and existing_json("missing_data_flags") == flags and
                existing_json("validation_errors") == entry["errors"] and bool(existing["image_valid"]) == entry["image_valid"] and
                bool(existing["sequence_valid"]) == entry["sequence_valid"] and
                existing_json("external_ids") == entry["external_ids"])
            key = "inserted" if not existing else ("unchanged" if unchanged else "updated")
            report[key] += 1
            if entry["errors"]:
                report["errors"].append({"species_id":sid,"errors":entry["errors"]})
        snapshots = conn.execute("""SELECT species_id,record_sha256,external_ids,taxon_id,resolution_status,missing_data_flags,
                     validation_errors,verified_its_sequence,image_valid,sequence_valid FROM fungip.species ORDER BY species_id""").fetchall()
        for snapshot in snapshots:
            snapshot["taxon_id"] = str(snapshot["taxon_id"]) if snapshot["taxon_id"] else None
            if isinstance(snapshot["external_ids"], str):
                snapshot["external_ids"] = json.loads(snapshot["external_ids"])
        conn.execute("INSERT INTO fungip.import_observation(catalog_sha256,report) VALUES (%s,%s::jsonb)",
                     (manifest["catalog_sha256"],canonical({"report":report,"qualification_snapshots":snapshots,
                        "validator_version":"fungip.validation.v2",
                        "identifier_derivation": derivation or {"version":"legacy_manifest",
                            "sha256":identifier_manifest_sha256(manifest["records"])},
                        "stored_identifier_snapshot_sha256":identifier_manifest_sha256(snapshots)})))
    return report


def main():
    parser = argparse.ArgumentParser(description="FungiP offline audit; explicit Cursor-only apply")
    parser.add_argument("package", type=Path)
    parser.add_argument("--duplicates-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifest = audit(args.package, args.duplicates_root)
    if args.apply:
        import os
        import psycopg
        from psycopg.rows import dict_row
        # DSN never appears in output or CLI. No default production endpoint.
        with psycopg.connect(os.environ["FUNGIP_IMPORT_DSN"], row_factory=dict_row) as conn:
            manifest["import_report"] = import_manifest(conn, manifest)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest.get("import_report", manifest["counts"])))


if __name__ == "__main__":
    main()
