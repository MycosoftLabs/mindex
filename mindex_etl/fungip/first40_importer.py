"""Explicit-connection first40 source importer; no CLI, file defaults, or connections.

Authored source only until separately qualified. The word "verified" is the user's
source designation; this never promotes token_attempt or claims new chain evidence.
"""
from __future__ import annotations

import json
import re
from .catalog import canonical, digest
from .first40 import validate_admitted_snapshot


LAUNCH_FIELDS = (
    "mint_address", "launch_tx", "launched_at", "launched_at_pt",
    "solana_explorer_url", "solana_explorer_tx_url", "solscan_tx_url",
    "solscan_token_url", "usepaid_url", "usepaid_short_url", "pumpfun_url",
    "metadata_uri", "recipient", "token_program", "decimals", "supply_raw",
)
RECORD_FIELDS = (
    "species_id", "ticker", "accepted_name", "dna_sha256", "dna_accession_version",
    "dna_database", "dna_source_url", "image_credit", "image_license", "image_sha256",
    "launch_status", "source_hash_match", "canonical_approved", "owner_entity",
) + LAUNCH_FIELDS + (
    "candidate_launch", "superseded_mints", "synonyms", "catalog_review_flags",
    "gap_flags", "verification_basis", "new_chain_verified",
)
JSON_FIELDS = {"candidate_launch"}
SPECIES_IDS = frozenset(f"FG{number:03d}" for number in range(1, 41))
UNSUPPLIED_FG026_FIELDS = (
    "launch_tx", "launched_at", "launched_at_pt", "solana_explorer_tx_url",
    "solscan_tx_url", "solscan_token_url", "usepaid_short_url", "token_program",
)


def _verify_envelope(snapshot: dict) -> None:
    """Recheck admission/digest before any connection method can be called."""
    bindings = snapshot.get("source_bindings")
    authority = snapshot.get("authority")
    if (type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1
            or snapshot.get("admissible") is not True or snapshot.get("disagreements") != []
            or not isinstance(bindings, dict)
            or set(bindings) != {"catalog_sha256", "launch_sha256", "handoff_sha256", "correction_sha256"}
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in bindings.values())
            or not isinstance(authority, dict)
            or set(authority) != {"approved", "approval_reference", "launch_schema", "superseded_encoding", "source_bindings", "correction_schema"}
            or authority.get("approved") is not True
            or authority.get("source_bindings") != bindings
            or not isinstance(authority.get("approval_reference"), str)
            or not authority["approval_reference"].strip()
            or authority.get("launch_schema") != "first40_launch_log_v1"
            or authority.get("correction_schema") != "direct_user_correction_v1"
            or authority.get("superseded_encoding") not in {"json", "semicolon"}
            or snapshot.get("corrected_input_version") != "direct_user_correction_v1"
            or not isinstance(snapshot.get("correction_recorded_at_utc"), str)
            or not snapshot["correction_recorded_at_utc"]):
        raise ValueError("First40 snapshot lacks exact source-bound admission authority")
    records = snapshot.get("records")
    if (not isinstance(records, list) or len(records) != 40
            or any(not isinstance(record, dict) for record in records)
            or {record.get("species_id") for record in records} != SPECIES_IDS):
        raise ValueError("First40 import requires exactly FG001 through FG040")
    expected = digest(canonical({key: value for key, value in snapshot.items()
                                 if key != "snapshot_sha256"}).encode("utf-8"))
    if snapshot.get("snapshot_sha256") != expected:
        raise ValueError("First40 snapshot digest mismatch")


def _identities(record: dict) -> tuple[list[str], list[str]]:
    candidate = record["candidate_launch"] or {}
    mints = [value for value in (record["mint_address"], candidate.get("mint_address")) if value]
    mints.extend(record["superseded_mints"])
    txs = [value for value in (record["launch_tx"], candidate.get("launch_tx")) if value]
    return mints, txs


def _check_collisions(records: list[dict]) -> None:
    owners = {"mint": {}, "transaction": {}}
    for record in records:
        mints, txs = _identities(record)
        for kind, values in (("mint", mints), ("transaction", txs)):
            for value in values:
                if value in owners[kind]:
                    raise ValueError(f"First40 {kind} collision requires explicit curation")
                owners[kind][value] = record["species_id"]


def _check_parent(record: dict, parent: dict | None) -> None:
    if parent is None:
        raise ValueError(f"First40 parent is absent: {record['species_id']}")
    science = parent["record"]
    if isinstance(science, str):
        science = json.loads(science)
    dna, image = science.get("dna") or {}, science.get("image") or {}
    matches = (
        parent.get("accepted_name") == record["accepted_name"],
        science.get("accepted_name") == record["accepted_name"],
        science.get("ticker") == record["ticker"],
        dna.get("sha256") == record["dna_sha256"],
        dna.get("accession_version") == record["dna_accession_version"],
        image.get("attribution") == record["image_credit"],
        image.get("license_code") == record["image_license"],
        image.get("sha256") == record["image_sha256"],
        parent.get("sequence_valid") is True,
        parent.get("image_valid") is True,
        bool(image),
    )
    if not all(matches):
        raise ValueError(f"First40 science/image parent qualification mismatch: {record['species_id']}")


def import_first40_snapshot(conn, snapshot: dict) -> dict:
    """Future Cursor calls with an explicit admitted snapshot and dict-row connection.

    One transaction shares the catalog import lock. Every preflight precedes inserts;
    an existing species under different source or payload requires future curation.
    A second identical import inserts/updates no batch or association records.
    """
    admitted = validate_admitted_snapshot(snapshot)
    _verify_envelope(admitted)
    records = sorted(admitted["records"], key=lambda record: record["species_id"])
    for record in records:
        if set(record) != set(RECORD_FIELDS):
            raise ValueError("First40 normalized record field contract mismatch")
        if (record["launch_status"] != "verified" or record["canonical_approved"] is not True
                or record["source_hash_match"] is not True or record["owner_entity"] != "MycoDAO"
                or record["verification_basis"] != "user_supplied_and_attached_verification"
                or record["new_chain_verified"] is not False or record["candidate_launch"] is not None
                or not isinstance(record["mint_address"], str) or not record["mint_address"]
                or (record["species_id"] != "FG026" and not record["launch_tx"])):
            raise ValueError("Corrected first40 source verification contract mismatch")
        if record["species_id"] == "FG026" and (
                any(record[field] is not None for field in UNSUPPLIED_FG026_FIELDS)
                or type(record["decimals"]) is not int or record["decimals"] != 6
                or record["supply_raw"] != "1000000000000000"):
            raise ValueError("FG026 must not inherit unsupplied superseded-mint evidence")
    if sum(len(record["superseded_mints"]) for record in records) != 5:
        raise ValueError("Corrected first40 snapshot requires exactly five preserved superseded mints")
    _check_collisions(records)
    sha = admitted["snapshot_sha256"]
    prepared = {record["species_id"]: (record, digest(canonical(record).encode("utf-8")))
                for record in records}
    report = {"snapshot_sha256": sha, "inserted": 0, "unchanged": 0,
              "verification_basis": "user_supplied_and_attached_verification",
              "new_chain_verified": False, "token_attempt_mutations": 0, "core_mutations": 0}
    with conn.transaction():
        isolation = conn.execute("SHOW transaction_isolation").fetchone()
        if not isolation or isolation.get("transaction_isolation") != "read committed":
            raise ValueError("First40 registry import requires READ COMMITTED lock visibility")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('fungip.catalog.import'))")
        parents = {row["species_id"]: row for row in conn.execute(
            "SELECT * FROM fungip.species WHERE species_id = ANY(%s) FOR SHARE",
            (sorted(SPECIES_IDS),)).fetchall()}
        existing_rows = conn.execute("SELECT * FROM fungip.first40_launch_association FOR UPDATE").fetchall()
        existing = {row["species_id"]: row for row in existing_rows}
        for sid, (record, payload_sha) in prepared.items():
            _check_parent(record, parents.get(sid))
            prior = existing.get(sid)
            if prior and (prior["snapshot_sha256"] != sha or prior["payload_sha256"] != payload_sha):
                raise ValueError(f"First40 source/identity retarget requires explicit future curation: {sid}")
        # Check newly inserted rows against every already stored registry identity.
        combined = list(records)
        combined.extend(row for row in existing_rows if row["species_id"] not in prepared)
        _check_collisions(combined)
        existing_batch = conn.execute(
            "SELECT snapshot_sha256 FROM fungip.first40_source_batch WHERE snapshot_sha256=%s FOR SHARE",
            (sha,)).fetchone()
        if not existing_batch:
            bindings, authority = admitted["source_bindings"], admitted["authority"]
            conn.execute("""INSERT INTO fungip.first40_source_batch(snapshot_sha256,schema_version,
                catalog_sha256,launch_sha256,handoff_sha256,correction_sha256,
                corrected_input_version,correction_recorded_at_utc,authority_approval_reference,
                launch_schema,superseded_encoding,authority,source_reported_as_of_utc,source_reported_as_of_pt)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                (sha,1,bindings["catalog_sha256"],bindings["launch_sha256"],bindings["handoff_sha256"],
                 bindings["correction_sha256"],admitted["corrected_input_version"],admitted["correction_recorded_at_utc"],
                 authority["approval_reference"],authority["launch_schema"],authority["superseded_encoding"],canonical(authority),
                 admitted["source_reported_as_of_utc"],admitted["source_reported_as_of_pt"]))
        columns = RECORD_FIELDS + ("snapshot_sha256", "payload_sha256")
        placeholders = ",".join("%s::jsonb" if field in JSON_FIELDS else "%s" for field in columns)
        sql = f"INSERT INTO fungip.first40_launch_association({','.join(columns)}) VALUES ({placeholders})"
        for sid, (record, payload_sha) in prepared.items():
            if sid in existing:
                report["unchanged"] += 1
                continue
            values = tuple(canonical(record[field]) if field in JSON_FIELDS and record[field] is not None
                           else record[field] for field in RECORD_FIELDS) + (sha, payload_sha)
            conn.execute(sql, values)
            report["inserted"] += 1
    return report
