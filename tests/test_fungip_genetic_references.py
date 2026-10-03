"""Actual captured-data projection + synthetic transaction tests; no DB/network."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PACKAGE = None
PREFLIGHT = None
from fungip_local_inputs import local_input


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


package = ModuleType("captured_reference_test")
package.__path__ = []
sys.modules[package.__name__] = package
catalog_path = REPO / "mindex_etl/fungip/catalog.py"
assert hashlib.sha256(catalog_path.read_bytes()).hexdigest() == "2504f25b41a3ce157faf7b721eb6ac695131668ba67a907477f656bfa35a2dcf"
catalog_module = load_module("captured_reference_test.catalog", catalog_path)
module_path = HERE / "genetic_references.py"
if not module_path.exists():
    module_path = REPO / "mindex_etl/fungip/genetic_references.py"
subject = load_module("captured_reference_test.genetic_references", module_path)


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchall(self):
        return copy.deepcopy(self.rows)

    def fetchone(self):
        return copy.deepcopy(self.rows[0]) if self.rows else None


class Transaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.before = copy.deepcopy(self.conn.existing)

    def __exit__(self, kind, value, traceback):
        if kind is not None:
            self.conn.existing = self.before
            self.conn.rolled_back = True
        elif self.conn.commit_failure:
            raise RuntimeError("synthetic lost commit acknowledgement")
        else:
            self.conn.committed = True


class Connection:
    """Rows model declared DB types; SQL is captured, never parsed/executed by a DB."""
    def __init__(self, catalog, preflight):
        self.info = SimpleNamespace(host="127.0.0.1", port=5547, dbname="fungip_native_20261002")
        self.target = {"db": self.info.dbname, "address": "127.0.0.1/32", "port": self.info.port}
        proofs = {r["species_id"]: r for r in preflight["records"]}
        self.sources, self.candidates = [], []
        for record in catalog["species"]:
            p = proofs[record["species_id"]]
            self.sources.append({"species_id": record["species_id"], "record": copy.deepcopy(record),
                "record_sha256": p["record_sha256"], "catalog_sha256": subject.CATALOG_SHA,
                "external_ids": catalog_module.source_ids(record), "sequence_valid": True,
                "validation_errors": [], "taxon_id": p["saved_canonical_uuid"],
                "resolution_status": "resolved" if p["saved_canonical_uuid"] else "unresolved"})
            for evidence in p["crosswalk_evidence"]:
                # UUID/provider keys are captured; kingdom/name/rank here are test-only DB states.
                self.candidates.append({"source": evidence["provider"], "external_id": evidence["external_id"],
                    "taxon_id": evidence["taxon_id"], "canonical_name": record["accepted_name"],
                    "rank": "species", "kingdom": "Fungi"})
        self.existing, self.statements = {}, []
        self.insert_attempts = 0
        self.fail_at = None
        self.race_at = None
        self.race_identical = False
        self.rolled_back = self.committed = self.commit_failure = False

    def transaction(self):
        return Transaction(self)

    def execute(self, sql, parameters=None):
        self.statements.append((sql, parameters))
        if sql.startswith("SET LOCAL") or "pg_advisory_xact_lock" in sql:
            return Result()
        if "current_database()" in sql:
            return Result([self.target])
        if "FROM fungip.species" in sql:
            return Result(self.sources)
        if "FROM core.taxon_external_id" in sql:
            return Result(self.candidates)
        if "FROM bio.genetic_sequence" in sql:
            if "accession=%s" in sql:
                return Result([self.existing[parameters[0]]] if parameters[0] in self.existing else [])
            aliases, versions = parameters
            return Result([r for r in self.existing.values() if r["accession"] in aliases or r.get("version") in versions])
        if sql.startswith("INSERT INTO bio.genetic_sequence"):
            self.insert_attempts += 1
            if self.insert_attempts == self.fail_at:
                raise RuntimeError("synthetic insert failure")
            row = dict(zip(subject.SEMANTIC_FIELDS, parameters))
            row["metadata"] = json.loads(row["metadata"])
            row["id"] = len(self.existing) + 1
            if self.insert_attempts == self.race_at:
                if not self.race_identical:
                    row["sequence"] = "ACGT"
                self.existing[row["accession"]] = row
                return Result()
            if row["accession"] in self.existing:
                return Result()
            self.existing[row["accession"]] = row
            return Result([{"id": row["id"]}])
        raise AssertionError("Unexpected SQL category")


class CapturedReferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global PACKAGE, PREFLIGHT
        PACKAGE = local_input("FUNGIP_CAPTURED_PACKAGE", directory=True)
        PREFLIGHT = local_input("FUNGIP_GENETICS_PREFLIGHT")
        cls.catalog = subject.read_bound_json(PACKAGE / "data/catalog.json", subject.CATALOG_SHA)
        cls.preflight = subject.read_bound_json(PREFLIGHT, subject.PREFLIGHT_SHA)
        cls.projection = subject.project(cls.catalog, cls.preflight, PACKAGE)
        cls.by_id = {r["metadata"]["fungip_capture"]["species_id"]: r for r in cls.projection["rows"]}
        cls.original = copy.deepcopy(cls.catalog)

    def connection(self):
        return Connection(self.catalog, self.preflight)

    def test_all_300_reference_sequences_retained_with_246_links_54_null(self):
        rows = self.projection["rows"]
        self.assertEqual(len(rows), 300)
        self.assertEqual(len({r["accession"] for r in rows}), 300)
        self.assertEqual(sum(r["taxon_id"] is not None for r in rows), 246)
        for original in self.catalog["species"]:
            row = self.by_id[original["species_id"]]
            self.assertEqual(row["sequence"], original["dna"]["sequence"])
            self.assertEqual(row["organism"], original["dna"]["organism"])
            self.assertEqual(row["metadata"]["fungip_capture"]["original_dna"], original["dna"])
        self.assertEqual(self.catalog, self.original)

    def test_fg015_is_712_base_reference_not_complete_its_or_genome(self):
        row = self.by_id["FG015"]
        self.assertEqual(row["taxon_id"], "8948f374-9439-4165-b592-33e3bf9a8998")
        self.assertEqual(row["accession"], "NR_151745.1")
        self.assertEqual(row["sequence_length"], 712)
        self.assertEqual(hashlib.sha256(row["sequence"].encode()).hexdigest(), "547d5885273ce70a71c8f8eb4186a70debba1f8dedde9090dd5cb4e4359e14ae")
        capture = row["metadata"]["fungip_capture"]
        self.assertFalse(capture["complete_its_supported"])
        self.assertFalse(capture["reference_is_whole_species_genome"])
        self.assertIsNone(row["region"])
        self.assertEqual(capture["original_dna"]["its_features"][0]["location"], "<1..305")

    def test_42_supported_its_never_replace_300_full_references(self):
        self.assertEqual(sum(r["metadata"]["fungip_capture"]["complete_its_supported"] for r in self.projection["rows"]), 42)
        for row in self.projection["rows"]:
            self.assertEqual(row["sequence"], row["metadata"]["fungip_capture"]["original_dna"]["sequence"])

    def test_six_resolved_source_organism_differences_stay_null(self):
        for sid in ["FG029", "FG035", "FG216", "FG279", "FG293", "FG300"]:
            with self.subTest(sid=sid):
                row = self.by_id[sid]
                self.assertIsNone(row["taxon_id"])
                self.assertIsNotNone(row["metadata"]["fungip_capture"]["saved_canonical_uuid"])
                self.assertNotEqual(row["organism"], row["metadata"]["fungip_capture"]["accepted_name"])

    def test_catalog_tamper_fails_before_any_connection(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["species"][0]["dna"]["sequence"] = "ACGT"
        with self.assertRaisesRegex(subject.Rejected, "record_binding"):
            subject.project(catalog, self.preflight, PACKAGE)

    def test_wrong_input_bytes_rejected(self):
        with self.assertRaisesRegex(subject.Rejected, "input_hash_mismatch"):
            subject.read_bound_json(PREFLIGHT, "0" * 64)

    def test_projection_duplicate_or_missing_identity_rejected(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["species"][-1] = catalog["species"][0]
        with self.assertRaisesRegex(subject.Rejected, "catalog_identity_set"):
            subject.project(catalog, self.preflight, PACKAGE)

    def test_literal_target_only(self):
        subject.validate_target(subject.TARGET)
        for key, value in [("host", "localhost"), ("host", "127.0.0.1.evil"), ("host", "192.0.2.1"),
                           ("port", "5432"), ("dbname", "mindex"), ("hostaddr", "127.0.0.1"),
                           ("service", "other"), ("options", "-c search_path=public")]:
            with self.subTest(key=key, value=value), self.assertRaises(subject.Rejected):
                subject.validate_target(subject.TARGET | {key: value})

    def test_connected_ip_is_exact_with_or_without_prefix(self):
        target = {"db": subject.TARGET["dbname"], "port": 5547, "address": "127.0.0.1/32"}
        subject.validate_connected_target(target)
        subject.validate_connected_target(target | {"address": "127.0.0.1"})
        for address in ["127.0.0.11/32", "127.0.0.1.evil", "192.0.2.1", None]:
            with self.subTest(address=address), self.assertRaises(subject.Rejected):
                subject.validate_connected_target(target | {"address": address})

    def test_full_batch_insert_then_exact_semantic_replay_is_no_op(self):
        conn = self.connection()
        first = subject.import_projection(conn, self.projection)
        self.assertEqual((first["inserted"], first["unchanged"], first["updated"]), (300, 0, 0))
        saved = copy.deepcopy(conn.existing)
        second = subject.import_projection(conn, self.projection)
        self.assertEqual((second["inserted"], second["unchanged"], second["updated"]), (0, 300, 0))
        self.assertEqual(conn.existing, saved)
        self.assertTrue(conn.committed)
        self.assertFalse(any("UPDATE bio.genetic_sequence" in sql or "DELETE" in sql for sql, _ in conn.statements))

    def test_conflicting_existing_accession_rejects_before_any_insert(self):
        conn = self.connection()
        row = copy.deepcopy(self.projection["rows"][-1])
        row["sequence"] = "ACGT"
        conn.existing[row["accession"]] = row
        before = copy.deepcopy(conn.existing)
        with self.assertRaisesRegex(subject.Rejected, "accession_conflict"):
            subject.import_projection(conn, self.projection)
        self.assertEqual(conn.insert_attempts, 0)
        self.assertEqual(conn.existing, before)

    def test_unversioned_accession_alias_is_not_overwritten_or_duplicated(self):
        conn = self.connection()
        row = copy.deepcopy(self.projection["rows"][0])
        row["accession"] = row["accession"].rsplit(".", 1)[0]
        conn.existing[row["accession"]] = row
        with self.assertRaisesRegex(subject.Rejected, "accession_conflict"):
            subject.import_projection(conn, self.projection)
        self.assertEqual(conn.insert_attempts, 0)

    def test_late_insert_failure_rolls_back_prior_batch_inserts(self):
        conn = self.connection()
        conn.fail_at = 9
        with self.assertRaises(RuntimeError):
            subject.import_projection(conn, self.projection)
        self.assertEqual(conn.existing, {})
        self.assertTrue(conn.rolled_back)
        self.assertFalse(conn.committed)

    def test_racing_conflict_rolls_back_prior_inserts(self):
        conn = self.connection()
        conn.race_at = 9
        with self.assertRaisesRegex(subject.Rejected, "concurrent_accession_conflict"):
            subject.import_projection(conn, self.projection)
        self.assertEqual(conn.existing, {})
        self.assertTrue(conn.rolled_back)

    def test_racing_identical_row_is_counted_unchanged_only(self):
        conn = self.connection()
        conn.race_at, conn.race_identical = 9, True
        result = subject.import_projection(conn, self.projection)
        self.assertEqual((result["inserted"], result["unchanged"]), (299, 1))

    def test_proposed_top_level_tampering_is_rejected_with_zero_changes(self):
        for key, value in [("sequence", "ACGT"), ("accession", "FAKE_123.1"), ("sequence_length", 4),
                           ("taxon_id", "00000000-0000-4000-8000-000000000001"), ("source", "invented")]:
            with self.subTest(key=key):
                projection = copy.deepcopy(self.projection)
                projection["rows"][0][key] = value
                conn = self.connection()
                with self.assertRaisesRegex(subject.Rejected, "projection_content_changed"):
                    subject.import_projection(conn, projection)
                self.assertEqual(conn.insert_attempts, 0)
                self.assertEqual(conn.existing, {})

    def test_direct_row_validator_also_rejects_semantic_tampering(self):
        conn = self.connection()
        row = copy.deepcopy(self.projection["rows"][0])
        row["sequence"] = "ACGT"
        with self.assertRaisesRegex(subject.Rejected, "proposed_semantics_changed"):
            subject.validate_source(row, conn.sources[0], conn.candidates)

    def test_metadata_flags_or_provenance_tampering_rejected_before_transaction(self):
        for key, value in [("complete_its_supported", True), ("dna_sidecar_sha256", "0" * 64),
                           ("specimen_identity", "fabricated common specimen")]:
            with self.subTest(key=key):
                projection = copy.deepcopy(self.projection)
                projection["rows"][14]["metadata"]["fungip_capture"][key] = value
                conn = self.connection()
                with self.assertRaisesRegex(subject.Rejected, "projection_content_changed"):
                    subject.import_projection(conn, projection)
                self.assertEqual(conn.statements, [])

    def test_stale_stored_record_or_uuid_is_rejected_before_inserts(self):
        for key, value in [("record_sha256", "0" * 64), ("taxon_id", None), ("sequence_valid", False)]:
            with self.subTest(key=key):
                conn = self.connection()
                conn.sources[0][key] = value
                with self.assertRaises(subject.Rejected):
                    subject.import_projection(conn, self.projection)
                self.assertEqual(conn.insert_attempts, 0)

    def test_actual_kingdom_rank_name_and_conflicting_provider_uuid_rechecked(self):
        for key, value in [("kingdom", "Undesignated"), ("rank", "genus"),
                           ("canonical_name", "Unrelated name"),
                           ("taxon_id", "00000000-0000-4000-8000-000000000002")]:
            with self.subTest(key=key):
                conn = self.connection()
                conn.candidates[0][key] = value
                with self.assertRaises(subject.Rejected):
                    subject.import_projection(conn, self.projection)
                self.assertEqual(conn.insert_attempts, 0)

    def test_oversize_or_missing_projection_and_unbounded_deadline_rejected(self):
        for count in [299, 301]:
            conn = self.connection()
            projection = copy.deepcopy(self.projection)
            projection["rows"] = (projection["rows"] + projection["rows"][:1])[:count]
            with self.assertRaises(subject.Rejected):
                subject.import_projection(conn, projection)
            self.assertEqual(conn.statements, [])
        with self.assertRaisesRegex(subject.Rejected, "deadline_not_bounded"):
            subject.import_projection(self.connection(), self.projection, deadline_seconds=121)

    def test_failed_commit_acknowledgement_is_unknown_not_rollback_success(self):
        conn = self.connection()
        conn.commit_failure = True
        with self.assertRaises(subject.CommitUnknown):
            subject.import_projection(conn, self.projection)
        self.assertFalse(conn.committed)
        self.assertFalse(conn.rolled_back)


if __name__ == "__main__":
    unittest.main(verbosity=2)
