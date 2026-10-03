"""Captured-record derivation + SQLite dialect-adapter tests; no native DB/provider.

The scientific records come from the frozen package audit. Core rows in MemorySQL
are finite test fixtures, not a replay or qualification of the real MINDEX export.
"""
import copy
import json
from functools import lru_cache
from pathlib import Path

import pytest
from fungip_local_inputs import local_input

from mindex_etl.fungip.catalog import (
    IDENTIFIER_DERIVATION_VERSION, canonical, digest,
    identifier_manifest_sha256, source_ids,
)
from mindex_etl.fungip.importer import import_manifest
from test_fungip_importer import MemorySQL


ROOT = Path(__file__).parents[1]
FIXTURE_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@lru_cache(maxsize=1)
def frozen_audit():
    raw = local_input("FUNGIP_CAPTURED_AUDIT").read_bytes()
    assert digest(raw) == "8b197c9a539b8b3ccf59d412a3ea608bfa10dcdee04cb3adc8dce6353e86eed6"
    return json.loads(raw)


def frozen_entry(sid):
    return copy.deepcopy(next(e for e in frozen_audit()["records"] if e["species_id"] == sid))


def manifest(entry, *, derived):
    entry = copy.deepcopy(entry)
    value = {"catalog_sha256": frozen_audit()["catalog_sha256"], "hash_conflicts": [],
             "duplicates": {}, "records": [entry]}
    if derived:
        entry["external_ids"] = source_ids(entry["record"])
        value["identifier_derivation"] = {
            "version": IDENTIFIER_DERIVATION_VERSION,
            "sha256": identifier_manifest_sha256(value["records"]),
        }
    return value


def add_core_fixture(db, entry, source, external_id, taxon_id=FIXTURE_UUID):
    db.db.execute("INSERT INTO taxon VALUES (?,?,?)",
                  (taxon_id, entry["record"]["accepted_name"], "species"))
    db.db.execute("INSERT INTO taxon_external_id VALUES (?,?,?)", (source, external_id, taxon_id))


@pytest.mark.parametrize("sid,gbif,col", [("FG034", "2549668", "6JWNR"), ("FG293", "2599345", "34FG8")])
def test_real_synonym_records_add_distinct_accepted_roles_without_changing_base(sid, gbif, col):
    e = frozen_entry(sid)
    before = canonical(e)
    ids = source_ids(e["record"])
    assert [x for x in ids if x.get("identifier_role") != "acceptedUsage"] == e["external_ids"]
    extra = [x for x in ids if x.get("identifier_role") == "acceptedUsage"]
    assert {(x["source"], x["external_id"]) for x in extra} == {("gbif", gbif), ("col_xr", col)}
    for identifier in extra:
        original = e["record"][identifier["source"]]
        assert identifier["matched_usage_external_id"] == str(original["response"]["usage"]["key"])
        assert identifier["source_url"] == original["source_url"]
        assert identifier["retrieved_at"] == e["record"]["retrieved_utc"]
        assert identifier["name"] == e["record"]["accepted_name"]
        assert identifier["rank"] == "SPECIES"
        assert identifier["provider_release"] is None
    assert canonical(e) == before


@pytest.mark.parametrize("mutation", ["rank", "name", "nonexact", "bool_key", "blank_key", "float_key", "bad_shape"])
def test_invalid_accepted_evidence_does_not_add_an_identifier(mutation):
    e = frozen_entry("FG034")
    response = e["record"]["gbif"]["response"]
    if mutation == "rank": response["acceptedUsage"]["rank"] = "GENUS"
    elif mutation == "name": response["acceptedUsage"]["canonicalName"] = "Ganoderma lucidum"
    elif mutation == "nonexact": response["diagnostics"]["matchType"] = "HIGHERRANK"
    elif mutation == "bool_key": response["acceptedUsage"]["key"] = True
    elif mutation == "blank_key": response["acceptedUsage"]["key"] = " "
    elif mutation == "float_key": response["acceptedUsage"]["key"] = 2549668.0
    else: response["acceptedUsage"] = ["2549668"]
    ids = source_ids(e["record"])
    assert not [x for x in ids if x["source"] == "gbif" and x.get("identifier_role") == "acceptedUsage"]
    assert any(x["source"] == "gbif" and x["external_id"] == "7690471" for x in ids)


def test_absent_accepted_usage_preserves_original_ids_and_same_key_does_not_duplicate():
    e = frozen_entry("FG034")
    for source in ("gbif", "col_xr"):
        e["record"][source]["response"].pop("acceptedUsage")
    assert source_ids(e["record"]) == e["external_ids"]
    e = frozen_entry("FG034")
    for source in ("gbif", "col_xr"):
        response = e["record"][source]["response"]
        response["acceptedUsage"]["key"] = response["usage"]["key"]
    assert source_ids(e["record"]) == e["external_ids"]


def test_actual_fg117_does_not_promote_variant_to_accepted_identity():
    e = frozen_entry("FG117")
    ids = source_ids(e["record"])
    assert not [x for x in ids if x["source"] == "gbif" and x.get("identifier_role") == "acceptedUsage"]
    assert [x for x in ids if x.get("identifier_role") != "acceptedUsage"] == e["external_ids"]


def test_all_300_preserve_raw_hashes_and_produce_34_collision_free_additions():
    added = []
    keys = []
    for original in frozen_audit()["records"]:
        e = copy.deepcopy(original)
        ids = source_ids(e["record"])
        assert e == original
        assert digest(canonical(e["record"]).encode()) == e["record_sha256"]
        assert [x for x in ids if x.get("identifier_role") != "acceptedUsage"] == e["external_ids"]
        added.extend(x for x in ids if x.get("identifier_role") == "acceptedUsage")
        keys.extend((x["source"], x["external_id"]) for x in ids)
    assert len(added) == 34
    assert len(keys) == len(set(keys)) == 918


@pytest.mark.parametrize("sid,accepted_key", [("FG034", "2549668"), ("FG293", "2599345")])
def test_real_record_requalification_persists_the_same_ids_used_for_lookup(sid, accepted_key):
    db = MemorySQL()
    entry = frozen_entry(sid)
    original = manifest(entry, derived=False)
    derived = manifest(entry, derived=True)
    add_core_fixture(db, entry, "gbif", accepted_key)
    assert import_manifest(db, original)["unresolved"] == 1
    original_import_run = db.db.execute("SELECT manifest FROM import_run").fetchone()[0]
    assert import_manifest(db, derived)["resolved"] == 1
    stored = dict(db.db.execute("SELECT * FROM species").fetchone())
    assert stored["taxon_id"] == FIXTURE_UUID
    assert json.loads(stored["external_ids"]) == derived["records"][0]["external_ids"]
    # Exercise persisted JSON -> source-qualified identity join, as the public
    # index does. SQLite JSON1 is not qualification of the PostgreSQL CTE.
    ids = db.db.execute("""SELECT DISTINCT external.taxon_id FROM species s,
        json_each(s.external_ids) identifier JOIN taxon_external_id external
        ON external.source=json_extract(identifier.value,'$.source')
        AND external.external_id=json_extract(identifier.value,'$.external_id')""").fetchall()
    assert [x[0] for x in ids] == [FIXTURE_UUID]
    assert json.loads(stored["record"]) == entry["record"]
    assert stored["record_sha256"] == entry["record_sha256"]
    assert stored["catalog_sha256"] == original["catalog_sha256"]
    assert db.db.execute("SELECT COUNT(*) FROM species_revision").fetchone()[0] == 1
    assert db.db.execute("SELECT manifest FROM import_run").fetchone()[0] == original_import_run
    assert import_manifest(db, derived)["unchanged"] == 1
    observations = [json.loads(x[0]) for x in db.db.execute("SELECT report FROM import_observation ORDER BY rowid")]
    assert len(observations) == 3
    assert observations[0]["identifier_derivation"]["version"] == "legacy_manifest"
    assert observations[-1]["identifier_derivation"] == derived["identifier_derivation"]
    assert observations[-1]["stored_identifier_snapshot_sha256"] == derived["identifier_derivation"]["sha256"]
    assert observations[-1]["qualification_snapshots"][0]["external_ids"] == derived["records"][0]["external_ids"]


def test_identifier_only_update_is_persisted_and_counted_before_second_import_is_unchanged():
    db = MemorySQL()
    entry = frozen_entry("FG126")
    # This fixture is already resolved through a legacy identifier. Only the
    # derived list changes, reproducing the old UPSERT omission.
    identifier = next(x for x in entry["external_ids"] if x["source"] == "inat")
    add_core_fixture(db, entry, identifier["source"], identifier["external_id"])
    old, new = manifest(entry, derived=False), manifest(entry, derived=True)
    assert old["records"][0]["external_ids"] != new["records"][0]["external_ids"]
    assert import_manifest(db, old)["resolved"] == 1
    assert import_manifest(db, new)["updated"] == 1
    assert json.loads(db.db.execute("SELECT external_ids FROM species").fetchone()[0]) == new["records"][0]["external_ids"]
    assert import_manifest(db, new)["unchanged"] == 1


@pytest.mark.parametrize("tamper", ["fingerprint", "identifier", "record_hash"])
def test_derived_manifest_tampering_fails_before_database_transaction(tamper):
    value = manifest(frozen_entry("FG034"), derived=True)
    if tamper == "fingerprint": value["identifier_derivation"]["sha256"] = "0" * 64
    elif tamper == "identifier": value["records"][0]["external_ids"][-1]["external_id"] = "unproven"
    else: value["records"][0]["record_sha256"] = "0" * 64
    if tamper != "fingerprint":
        value["identifier_derivation"]["sha256"] = identifier_manifest_sha256(value["records"])
    class NoDatabaseCalls:
        def transaction(self): raise AssertionError("must reject before a DB call")
    with pytest.raises(ValueError): import_manifest(NoDatabaseCalls(), value)


def test_added_identity_does_not_retarget_a_known_uuid_or_discard_conflicts():
    db = MemorySQL()
    entry = frozen_entry("FG034")
    usage = next(x for x in entry["external_ids"] if x["source"] == "gbif")
    add_core_fixture(db, entry, "gbif", usage["external_id"])
    assert import_manifest(db, manifest(entry, derived=False))["resolved"] == 1
    add_core_fixture(db, entry, "gbif", "2549668", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    result = import_manifest(db, manifest(entry, derived=True))
    assert result["conflicts"] == [{"species_id":"FG034","reason":"mapping_changed_requires_curation"}]
    assert db.db.execute("SELECT taxon_id FROM species").fetchone()[0] == FIXTURE_UUID


def test_first40_snapshot_bytes_remain_the_frozen_parent_bound_input():
    raw = local_input("FUNGIP_FIRST40_SNAPSHOT").read_bytes()
    assert digest(raw) == "e8d3cc8220ac0b2081d75076c9e4fca41feb79dd2711c842a460e4def89b937b"
