"""Offline real-data projection controls, not PostgreSQL or chain qualification.

Uses the accepted 40-record snapshot and actual 300-catalog parent records. The
session below only returns DDL-shaped rows and checks selected column names;
it neither executes SQL nor connects to a database. Native aware datetimes model
timestamptz readback. No validator, launch fields, or application DTO is mocked.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fungip_local_inputs import local_input

from mindex_api.contracts.v1.ancestry_index import FungiPLaunchAssociation
from mindex_api.routers.taxon import _fungip_taxon_index_row, list_fungip_taxon_index
from mindex_api.services import ancestry_public_members as ancestry
from mindex_etl.fungip.first40_detail import PUBLIC_TIMES, public_first40_launch


ROOT = Path(__file__).resolve().parents[1]
SPECIES_IDS = tuple(f"FG{number:03d}" for number in range(1, 41))
PROVENANCE = (
    "snapshot_sha256", "payload_sha256", "catalog_sha256", "launch_sha256",
    "handoff_sha256", "correction_sha256", "launch_schema", "superseded_encoding",
    "corrected_input_version", "authority_approval_reference",
)
NULL_FG026 = (
    "launch_tx", "launched_at", "launched_at_pt", "solana_explorer_tx_url",
    "solscan_tx_url", "solscan_token_url", "usepaid_short_url", "token_program",
)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical_hash(value):
    # Independent reproduction of the importer's documented canonical JSON hash.
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8"))


def _timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _columns(ddl, table):
    block = re.search(r"CREATE TABLE fungip\." + table + r" \((.*?)\n\);", ddl, re.S)
    assert block, table
    return set(re.findall(r"^  ([a-z][a-z0-9_]*)\s+(?:text|smallint|boolean|timestamptz|jsonb)\b",
                          block.group(1), re.M))


@pytest.fixture(scope="module")
def source():
    snapshot_bytes = local_input("FUNGIP_FIRST40_SNAPSHOT").read_bytes()
    assert _sha(snapshot_bytes) == "e8d3cc8220ac0b2081d75076c9e4fca41feb79dd2711c842a460e4def89b937b"
    audit_bytes = local_input("FUNGIP_CAPTURED_AUDIT").read_bytes()
    # This checkout is the exact LF normalization of accepted CRLF artifact 418314fc….
    assert _sha(audit_bytes.replace(b"\r\n", b"\n")) == "8b197c9a539b8b3ccf59d412a3ea608bfa10dcdee04cb3adc8dce6353e86eed6"
    ddl_bytes = (ROOT / "migrations/fungip_002_first40_launches.sql").read_bytes()
    assert _sha(ddl_bytes) == "fc36efdb63c7975b6b55606cc39102a5b2c0a1616ba668c7141800e4ae457708"
    snapshot, audit = json.loads(snapshot_bytes), json.loads(audit_bytes)
    assert tuple(row["species_id"] for row in snapshot["records"]) == SPECIES_IDS
    assert snapshot["snapshot_sha256"] == _canonical_hash({
        key: value for key, value in snapshot.items() if key != "snapshot_sha256"
    })
    assert snapshot["admissible"] is True and snapshot["disagreements"] == []
    assert len(audit["records"]) == 300
    entries = {entry["species_id"]: entry for entry in audit["records"]}
    assert len(entries) == 300
    return snapshot, audit, entries, ddl_bytes.decode("utf-8")


def _database_rows(source):
    snapshot, audit, entries, ddl = source
    association_columns = _columns(ddl, "first40_launch_association")
    batch_columns = _columns(ddl, "first40_source_batch")
    authority = snapshot["authority"]
    batch = {
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "schema_version": snapshot["schema_version"],
        **snapshot["source_bindings"],
        "corrected_input_version": snapshot["corrected_input_version"],
        "correction_recorded_at_utc": _timestamp(snapshot["correction_recorded_at_utc"]),
        "authority_approval_reference": authority["approval_reference"],
        "launch_schema": authority["launch_schema"],
        "superseded_encoding": authority["superseded_encoding"],
        "authority": copy.deepcopy(authority),
        "source_reported_as_of_utc": _timestamp(snapshot["source_reported_as_of_utc"]),
        "source_reported_as_of_pt": snapshot["source_reported_as_of_pt"],
        "verification_basis": "user_supplied_and_attached_verification",
        "new_chain_verified": False,
        # Synthetic storage bookkeeping only; never asserted as an actual import time.
        "admitted_at": _timestamp(snapshot["correction_recorded_at_utc"]),
    }
    assert set(batch) == batch_columns
    rows, indexed = {}, {}
    for launch in snapshot["records"]:
        sid = launch["species_id"]
        entry = entries[sid]
        association = {
            **copy.deepcopy(launch), "snapshot_sha256": snapshot["snapshot_sha256"],
            "payload_sha256": _canonical_hash(launch),
            "imported_at": batch["admitted_at"],
        }
        association["launched_at"] = _timestamp(association["launched_at"])
        assert set(association) == association_columns
        parent = {
            "species_id": sid, "accepted_name": entry["record"]["accepted_name"],
            "record": copy.deepcopy(entry["record"]),
            "image_valid": entry["image_valid"], "sequence_valid": entry["sequence_valid"],
        }
        rows[sid] = {"association": association, "batch": copy.deepcopy(batch), "species": parent}
        # Unresolved read fixture: no manufactured canonical UUID or chain receipt.
        indexed[sid] = {
            **copy.deepcopy(entry), "accepted_name": parent["accepted_name"],
            "catalog_sha256": audit["catalog_sha256"],
            "validation_errors": copy.deepcopy(entry["errors"]),
            "stored_taxon_id": None, "resolution_status": "unresolved",
            "candidate_count": 0, "candidate_taxon_id": None, "candidate_taxon_ids": [],
            "canonical_name": None, "canonical_rank": None, "canonical_kingdom": None,
            "page_taxon_id": None, "page_record_sha256": None, "page_evidence": None,
            "page_canonical_url": None, "token_confirmed": False,
        }
    return rows, indexed, {"association": association_columns, "batch": batch_columns}


class _Result:
    def __init__(self, *, rows=(), one=None):
        self.rows, self.single = list(rows), one

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def one(self):
        return self.single


class _ReadRows:
    """Finite SELECT recorder, not a SQL engine or database assertion."""
    def __init__(self, rows, indexed, columns):
        self.rows, self.indexed, self.columns = rows, indexed, columns
        self.calls, self.rollback_calls = [], 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "FROM fungip.first40_launch_association association" in sql and "JOIN fungip.first40_source_batch batch" in sql:
            select = sql.split("FROM fungip.first40_launch_association association", 1)[0]
            select = re.sub(r"^\s*SELECT\s+", "", select, flags=re.I)
            projection = []
            for expression in select.split(","):
                match = re.fullmatch(r"\s*(association|batch|species)\.(\*|[a-z0-9_]+)(?:\s+AS\s+([a-z0-9_]+))?\s*", expression, re.I)
                assert match, "Unsupported offline SELECT shape; inspect source rather than emulate SQL"
                alias, column, rename = match.groups()
                if alias in self.columns and column != "*":
                    assert column in self.columns[alias], f"Selected column absent from frozen DDL: {alias}.{column}"
                projection.append((alias, column, rename))
            output = []
            for joined in self.rows.values():
                row = {}
                for alias, column, rename in projection:
                    if column == "*":
                        row.update(copy.deepcopy(joined[alias]))
                    else:
                        assert column in joined[alias], f"Unexpected parent/select column: {alias}.{column}"
                        row[rename or column] = copy.deepcopy(joined[alias][column])
                output.append(row)
            return _Result(rows=output)
        if "to_regclass('fungip.species') AS species_table" in sql:
            return _Result(one={"species_table": "fungip.species", "launch_table": "fungip.first40_launch_association", "batch_table": "fungip.first40_source_batch"})
        if "COUNT(*)::int AS total" in sql and "FROM indexed" in sql:
            return _Result(one={"total": len(self.indexed), "linked": 0, "unresolved": len(self.indexed), "ambiguous": 0})
        if "SELECT indexed.*" in sql:
            return _Result(rows=list(self.indexed.values())[params["offset"]:params["offset"] + params["limit"]])
        if "AS associations" in sql and "AS exclusions" in sql:
            return _Result(one={"associations": len(self.rows), "exclusions": len({mint for row in self.rows.values() for mint in row["association"]["superseded_mints"]})})
        raise AssertionError("Unexpected query in finite real-snapshot read fixture")

    async def rollback(self):
        self.rollback_calls += 1


@pytest.mark.parametrize("species_id", SPECIES_IDS)
def test_actual_record_projects_native_timestamps_with_all_required_provenance(source, species_id):
    rows, indexed, columns = _database_rows(source)
    joined = rows[species_id]
    session = _ReadRows({species_id: joined}, {species_id: indexed[species_id]}, columns)
    valid, invalid = asyncio.run(ancestry.load_validated_first40_associations(session))
    assert invalid == set() and set(valid) == {species_id}
    expected = copy.deepcopy(joined["association"])
    expected.update({key: joined["batch"][key] for key in PUBLIC_TIMES})
    for key in ("launched_at", "source_reported_as_of_utc", "correction_recorded_at_utc"):
        value = expected[key]
        if isinstance(value, datetime):
            assert value.tzinfo is not None
            expected[key] = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    public = public_first40_launch(expected, joined["species"])
    actual = valid[species_id]
    assert {key: actual[key] for key in public} == public
    for key in PROVENANCE:
        assert actual[key] == joined["association"].get(key, joined["batch"].get(key)), key
    assert set(actual) == set(public) | set(PROVENANCE)
    dto = FungiPLaunchAssociation(**actual)
    assert dto.launched_at == joined["association"]["launched_at"]
    assert dto.source_reported_as_of_utc == joined["batch"]["source_reported_as_of_utc"]
    member = _fungip_taxon_index_row(indexed[species_id], actual).fungip
    assert member.first40_launch == dto
    assert member.launch_association_state == "source_verified"
    assert member.mindex_uuid is None and member.canonical_taxon_uuid is None
    assert member.token_confirmed is False and member.chain_receipt_status == "not_confirmed"
    assert member.first40_launch.new_chain_verified is False
    assert len(session.calls) == 1 and session.rollback_calls == 0


def _index_response(session):
    return asyncio.run(list_fungip_taxon_index(
        q=None, offset=0, limit=300, kingdom=None, rank=None, prefix=None,
        order_by="canonical_name", order="asc", db=session,
    ))


def test_all40_actual_records_construct_index_response_and_preserve_corrections(source):
    session = _ReadRows(*_database_rows(source))
    response = _index_response(session)
    assert response.pagination.total == 40 and len(response.data) == 40
    records = {item.fungip.species_id: item.fungip.first40_launch for item in response.data}
    assert set(records) == set(SPECIES_IDS)
    assert len({item.mint_address for item in records.values()}) == 40
    excluded = {mint for item in records.values() for mint in item.superseded_mints}
    assert len(excluded) == 5
    assert not excluded.intersection(item.mint_address for item in records.values())
    assert response.counts.launch_associations == 40 and response.counts.superseded_exclusions == 5
    assert all(item.fungip.launch_association_state == "source_verified" for item in response.data)
    fg026 = records["FG026"]
    assert all(getattr(fg026, field) is None for field in NULL_FG026)
    assert all("blank_" + field in fg026.gap_flags for field in NULL_FG026)
    original = next(row for row in source[0]["records"] if row["species_id"] == "FG026")
    assert fg026.mint_address == original["mint_address"]
    assert fg026.decimals == 6 and fg026.supply_raw == "1000000000000000"
    assert records["FG033"].accepted_name == "Ganoderma lucidum"
    assert records["FG034"].accepted_name == "Ganoderma sichuanense"
    assert records["FG034"].synonyms == ["Ganoderma lingzhi"]
    assert records["FG033"].synonyms == []
    assert records["FG021"].launched_at == _timestamp("2026-10-02T22:16:19Z")
    assert all(item.new_chain_verified is False for item in records.values())
    assert session.rollback_calls == 0


@pytest.mark.parametrize("field", ("accepted_name", "record.accepted_name", "record.ticker",
                                  "record.dna.sha256", "record.image.sha256",
                                  "image_valid", "sequence_valid"))
def test_stale_real_parent_withholds_launch_and_reports_invalid_binding(source, field):
    rows, indexed, columns = _database_rows(source)
    parent = rows["FG026"]["species"]
    keys = field.split(".")
    target = parent
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = False if field.endswith("_valid") else "changed-parent-fixture"
    response = _index_response(_ReadRows(rows, indexed, columns))
    by_id = {item.fungip.species_id: item.fungip for item in response.data}
    assert len(by_id) == 40
    assert by_id["FG026"].first40_launch is None
    assert by_id["FG026"].launch_association_state == "invalid_binding"
    assert by_id["FG026"].token_confirmed is False
    assert all(item.first40_launch is not None and item.launch_association_state == "source_verified"
               for sid, item in by_id.items() if sid != "FG026")


def test_naive_storage_timestamp_is_not_silently_qualified(source):
    rows, indexed, columns = _database_rows(source)
    rows["FG021"]["association"]["launched_at"] = rows["FG021"]["association"]["launched_at"].replace(tzinfo=None)
    valid, invalid = asyncio.run(ancestry.load_validated_first40_associations(_ReadRows(rows, indexed, columns)))
    assert invalid == {"FG021"} and set(valid) == set(SPECIES_IDS) - {"FG021"}
