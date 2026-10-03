"""AUTHORED, NOT EXECUTED. Future offline importer orchestration controls only.

The fake connection records a plan; it does not execute SQL or qualify PostgreSQL.
Plan controls stub admission; one composition control uses the actual parser with
four synthetic buffers. No supplied datasets are imported by these fixture helpers.
"""
import contextlib
import copy
import json
import unittest
from unittest.mock import patch
from mindex_etl.fungip.catalog import canonical, digest
from mindex_etl.fungip.first40_importer import LAUNCH_FIELDS, RECORD_FIELDS, UNSUPPLIED_FG026_FIELDS, import_first40_snapshot


def _base58(value):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, digit = divmod(number, 58)
        encoded = alphabet[digit] + encoded
    return "1" * (len(value) - len(value.lstrip(b"\0"))) + encoded


def _launch(number):
    mint = _base58(bytes([number]) * 32)
    tx = _base58(bytes([number]) * 64)
    values = dict.fromkeys(LAUNCH_FIELDS)
    values.update(mint_address=mint, launch_tx=tx, launched_at="2026-10-02T19:00:00Z",
                  launched_at_pt="2026-10-02 12:00:00 PDT", metadata_uri="https://ipfs.io/ipfs/example",
                  recipient="@nodefather", decimals=6, supply_raw="1000000000")
    return values


def _seal(snapshot):
    snapshot["snapshot_sha256"] = digest(canonical({key: value for key, value in snapshot.items()
                                                   if key != "snapshot_sha256"}).encode("utf-8"))
    return snapshot


def _snapshot():
    records = []
    for number in range(1, 41):
        sid = f"FG{number:03d}"
        record = dict.fromkeys(RECORD_FIELDS)
        record.update(species_id=sid, ticker=f"F{number:03d}", accepted_name=f"Example species {number}",
                      dna_sha256="a" * 64, dna_accession_version=f"AB{number:06d}.1", dna_database="GenBank",
                      dna_source_url=f"https://www.ncbi.nlm.nih.gov/nuccore/AB{number:06d}.1",
                      image_credit=f"Synthetic attribution {number}", image_license="cc-by-4.0",
                      image_sha256="b" * 64, launch_status="verified", source_hash_match=True,
                      canonical_approved=True, owner_entity="MycoDAO", candidate_launch=None,
                      superseded_mints=[], synonyms=["Ganoderma lingzhi"] if number == 34 else [],
                      catalog_review_flags=["synthetic_fixture"], gap_flags=[],
                      verification_basis="user_supplied_and_attached_verification", new_chain_verified=False)
        record.update(_launch(number))
        if number in (1, 2, 3, 21, 26):
            record["superseded_mints"] = [_base58(bytes([number + 100]) * 32)]
        if number == 26:
            for field in UNSUPPLIED_FG026_FIELDS:
                record[field] = None
            record["supply_raw"] = "1000000000000000"
            record["gap_flags"] = ["launch_tx_missing", "token_program_missing"]
        records.append(record)
    bindings = {"catalog_sha256": "c" * 64, "launch_sha256": "d" * 64,
                "handoff_sha256": "e" * 64, "correction_sha256": "f" * 64}
    return _seal({"schema_version": 1, "source_bindings": bindings,
                  "authority": {"approved": True, "approval_reference": "synthetic human authority reference",
                                "launch_schema": "first40_launch_log_v1", "superseded_encoding": "semicolon",
                                "correction_schema": "direct_user_correction_v1",
                                "source_bindings": copy.deepcopy(bindings)},
                  "source_reported_as_of_utc": "2026-10-02T23:20:00Z",
                  "corrected_input_version": "direct_user_correction_v1",
                  "correction_recorded_at_utc": "2026-10-02T23:15:34Z",
                  "source_reported_as_of_pt": "2026-10-02 16:20:00 PT", "records": records,
                  "disagreements": [], "admissible": True})


class _Result:
    def __init__(self, rows=()):
        self.rows = copy.deepcopy(list(rows))
    def fetchall(self):
        return self.rows
    def fetchone(self):
        return self.rows[0] if self.rows else None


class PlanConnection:
    """Plan recorder with transactional rollback; no database or SQL evaluation."""
    def __init__(self, snapshot):
        self.calls, self.rows, self.batches = [], {}, set()
        self.isolation = "read committed"
        self.fail_after_association_inserts = None
        self.excluded_state = {
            "FG041_FG300": {f"FG{number:03d}": {"unchanged": True} for number in range(41, 301)},
            "core_taxa": {"existing-core-taxon": {"unchanged": True}},
            "token_attempt": {"existing-attempt": {"status": "submitted-unknown"}},
            "token_event": [{"unchanged": True}],
        }
        self.parents = {}
        for record in snapshot["records"]:
            self.parents[record["species_id"]] = {
                "species_id": record["species_id"], "accepted_name": record["accepted_name"],
                "sequence_valid": True, "image_valid": True,
                "record": {"accepted_name": record["accepted_name"], "ticker": record["ticker"],
                           "dna": {"sha256": record["dna_sha256"], "accession_version": record["dna_accession_version"]},
                           "image": {"attribution": record["image_credit"], "license_code": record["image_license"],
                                     "sha256": record["image_sha256"]}}}
    @contextlib.contextmanager
    def transaction(self):
        saved = copy.deepcopy((self.rows, self.batches))
        try:
            yield
        except Exception:
            self.rows, self.batches = saved
            raise
    def execute(self, sql, params=()):
        self.calls.append((sql, copy.deepcopy(params)))
        if sql == "SHOW transaction_isolation":
            return _Result([{"transaction_isolation": self.isolation}])
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if sql.startswith("SELECT * FROM fungip.species"):
            return _Result(self.parents.values())
        if sql.startswith("SELECT * FROM fungip.first40_launch_association"):
            return _Result(self.rows.values())
        if sql.startswith("SELECT snapshot_sha256 FROM fungip.first40_source_batch"):
            return _Result([{"snapshot_sha256": params[0]}] if params[0] in self.batches else [])
        if sql.startswith("INSERT INTO fungip.first40_source_batch"):
            self.batches.add(params[0])
            return _Result()
        if sql.startswith("INSERT INTO fungip.first40_launch_association"):
            if (self.fail_after_association_inserts is not None
                    and len(self.rows) >= self.fail_after_association_inserts):
                raise RuntimeError("Synthetic connection failure after partial association insert")
            columns = RECORD_FIELDS + ("snapshot_sha256", "payload_sha256")
            row = dict(zip(columns, params))
            if row["candidate_launch"] is not None:
                row["candidate_launch"] = json.loads(row["candidate_launch"])
            self.rows[row["species_id"]] = row
            return _Result()
        raise AssertionError("Unexpected or excluded SQL in first40 plan")


class First40ImporterPlanTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = _snapshot()
        self.connection = PlanConnection(self.snapshot)
        self.admission = patch("mindex_etl.fungip.first40_importer.validate_admitted_snapshot", side_effect=copy.deepcopy)
        self.admission.start()
        self.addCleanup(self.admission.stop)

    def test_identical_second_import_has_no_record_mutations(self):
        first = import_first40_snapshot(self.connection, self.snapshot)
        self.assertEqual(first["inserted"], 40)
        self.connection.calls.clear()
        second = import_first40_snapshot(self.connection, self.snapshot)
        self.assertEqual(second["unchanged"], 40)
        self.assertFalse(any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql, _ in self.connection.calls))
        self.assertEqual(len(self.connection.rows), 40)
        self.assertEqual(len(self.connection.batches), 1)

    def test_authority_source_binding_mismatch_rejects_before_connection(self):
        self.snapshot["authority"]["source_bindings"]["launch_sha256"] = "f" * 64
        _seal(self.snapshot)
        with self.assertRaises(ValueError):
            import_first40_snapshot(self.connection, self.snapshot)
        self.assertEqual(self.connection.calls, [])

    def test_digest_disagreement_extra_species_and_unapproved_reject_before_connection(self):
        for mutation in (lambda snapshot: snapshot.update(snapshot_sha256="0" * 64),
                         lambda snapshot: snapshot.update(disagreements=["unresolved authority"]),
                         lambda snapshot: snapshot["records"][39].update(species_id="FG041"),
                         lambda snapshot: snapshot["authority"].update(approved=False)):
            snapshot = copy.deepcopy(self.snapshot)
            mutation(snapshot)
            with self.assertRaises(ValueError):
                import_first40_snapshot(self.connection, snapshot)
            self.assertEqual(self.connection.calls, [])

    def test_parent_name_ticker_dna_and_image_mismatches_precede_any_insert(self):
        for key, value in (("accepted_name", "Another name"), ("sequence_valid", False), ("image_valid", False)):
            connection = PlanConnection(self.snapshot)
            connection.parents["FG040"][key] = value
            with self.assertRaises(ValueError):
                import_first40_snapshot(connection, self.snapshot)
            self.assertFalse(any(sql.startswith("INSERT") for sql, _ in connection.calls))
        for section, key in (("dna", "sha256"), ("dna", "accession_version"), ("image", "attribution"),
                             ("image", "license_code"), ("image", "sha256")):
            connection = PlanConnection(self.snapshot)
            connection.parents["FG040"]["record"][section][key] = "mismatch"
            with self.assertRaises(ValueError):
                import_first40_snapshot(connection, self.snapshot)
            self.assertEqual(connection.rows, {})
        connection = PlanConnection(self.snapshot)
        connection.parents["FG040"]["record"]["ticker"] = "OTHER"
        with self.assertRaises(ValueError):
            import_first40_snapshot(connection, self.snapshot)
        self.assertEqual(connection.rows, {})

    def test_candidate_superseded_and_transaction_collision_rejected(self):
        for mutate in (lambda records: records[20].update(candidate_launch=_launch(21)),
                       lambda records: records[25].update(superseded_mints=[records[0]["mint_address"]]),
                       lambda records: records[20].update(launch_tx=records[0]["launch_tx"])):
            snapshot = copy.deepcopy(self.snapshot)
            mutate(snapshot["records"])
            _seal(snapshot)
            with self.assertRaises(ValueError):
                import_first40_snapshot(self.connection, snapshot)
            self.assertEqual(self.connection.calls, [])

    def test_retarget_or_source_rebind_requires_future_curation(self):
        import_first40_snapshot(self.connection, self.snapshot)
        for change in (lambda snapshot: snapshot["records"][0].update(mint_address=_base58(bytes([100]) * 32)),
                       lambda snapshot: snapshot["authority"].update(approval_reference="a different source admission")):
            snapshot = copy.deepcopy(self.snapshot)
            change(snapshot)
            _seal(snapshot)
            self.connection.calls.clear()
            with self.assertRaises(ValueError):
                import_first40_snapshot(self.connection, snapshot)
            self.assertFalse(any(sql.startswith("INSERT") for sql, _ in self.connection.calls))

    def test_fg026_cannot_inherit_superseded_transaction_time_or_program(self):
        for field in UNSUPPLIED_FG026_FIELDS:
            snapshot = copy.deepcopy(self.snapshot)
            snapshot["records"][25][field] = "inherited obsolete evidence"
            _seal(snapshot)
            with self.assertRaises(ValueError):
                import_first40_snapshot(self.connection, snapshot)
            self.assertEqual(self.connection.calls, [])

    def test_repeatable_read_cannot_hide_identity_collisions_after_lock_wait(self):
        self.connection.isolation = "repeatable read"
        with self.assertRaises(ValueError):
            import_first40_snapshot(self.connection, self.snapshot)
        self.assertFalse(any(sql.startswith("INSERT") for sql, _ in self.connection.calls))

    def test_real_four_buffer_admission_composes_with_importer_plan(self):
        # unittest discovery supplies the test directory; this fixture never reads
        # the user's catalog/log/handoff/correction files or contacts a provider.
        from test_fungip_first40 import SyntheticSources
        from mindex_etl.fungip.first40 import build_first40_snapshot
        self.admission.stop()
        arguments = SyntheticSources().arguments(dialect="first40_launch_log_v1", encoding="semicolon")
        snapshot = build_first40_snapshot(*arguments)
        self.assertTrue(snapshot["admissible"])
        self.assertEqual(snapshot["disagreements"], [])
        connection = PlanConnection(snapshot)
        first = import_first40_snapshot(connection, snapshot)
        self.assertEqual(first["inserted"], 40)
        self.assertTrue(all(row["launch_status"] == "verified" and not row["new_chain_verified"]
                            for row in connection.rows.values()))
        self.assertIsNotNone(connection.rows["FG021"]["launch_tx"])
        self.assertTrue(all(connection.rows["FG026"][field] is None for field in UNSUPPLIED_FG026_FIELDS))
        self.assertEqual(sum(len(row["superseded_mints"]) for row in connection.rows.values()), 5)
        connection.calls.clear()
        second = import_first40_snapshot(connection, snapshot)
        self.assertEqual(second["unchanged"], 40)
        self.assertFalse(any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql, _ in connection.calls))
        changed = copy.deepcopy(snapshot)
        changed["records"][0]["mint_address"] = _base58(bytes([200]) * 32)
        connection.calls.clear()
        with self.assertRaises(ValueError):
            import_first40_snapshot(connection, changed)
        self.assertEqual(connection.calls, [])

    def test_partial_insert_failure_rolls_back_fake_batch_rows_and_preserves_inputs(self):
        # This controls orchestration against a fake transactional plan; it does
        # not attest native PostgreSQL rollback, triggers, or concurrent writers.
        input_before = copy.deepcopy(self.snapshot)
        parent_before = copy.deepcopy(self.connection.parents)
        excluded_before = copy.deepcopy(self.connection.excluded_state)
        self.connection.fail_after_association_inserts = 3
        with self.assertRaisesRegex(RuntimeError, "partial association insert"):
            import_first40_snapshot(self.connection, self.snapshot)
        self.assertEqual(self.connection.rows, {})
        self.assertEqual(self.connection.batches, set())
        self.assertEqual(self.snapshot, input_before)
        self.assertEqual(self.connection.parents, parent_before)
        self.assertEqual(self.connection.excluded_state, excluded_before)
        association_calls = [sql for sql, _ in self.connection.calls
                             if sql.startswith("INSERT INTO fungip.first40_launch_association")]
        self.assertEqual(len(association_calls), 4)
        self.assertTrue(all("core." not in sql and "token_attempt" not in sql and "token_event" not in sql
                            for sql, _ in self.connection.calls))
        parent_reads = [params for sql, params in self.connection.calls if sql.startswith("SELECT * FROM fungip.species")]
        self.assertEqual(parent_reads, [([f"FG{number:03d}" for number in range(1, 41)],)])

    def test_special_rows_and_no_core_or_token_attempt_mutation(self):
        report = import_first40_snapshot(self.connection, self.snapshot)
        canonical, corrected = self.connection.rows["FG021"], self.connection.rows["FG026"]
        self.assertIsNotNone(canonical["mint_address"])
        self.assertIsNotNone(canonical["launch_tx"])
        self.assertTrue(all(corrected[field] is None for field in UNSUPPLIED_FG026_FIELDS))
        self.assertEqual(corrected["supply_raw"], "1000000000000000")
        for row in (canonical, corrected):
            self.assertIsNone(row["candidate_launch"])
            self.assertEqual(row["owner_entity"], "MycoDAO")
            self.assertTrue(row["canonical_approved"])
            self.assertEqual(row["launch_status"], "verified")
            self.assertEqual(row["verification_basis"], "user_supplied_and_attached_verification")
            self.assertFalse(row["new_chain_verified"])
        self.assertEqual(self.connection.rows["FG034"]["synonyms"], ["Ganoderma lingzhi"])
        self.assertEqual(self.connection.rows["FG033"]["synonyms"], [])
        self.assertNotEqual(self.connection.rows["FG033"]["accepted_name"], self.connection.rows["FG034"]["accepted_name"])
        self.assertEqual(sum(row["source_hash_match"] is True for row in self.connection.rows.values()), 40)
        self.assertEqual(sum(row["canonical_approved"] is True for row in self.connection.rows.values()), 40)
        self.assertEqual(sum(len(row["superseded_mints"]) for row in self.connection.rows.values()), 5)
        self.assertEqual(report["core_mutations"], 0)
        self.assertEqual(report["token_attempt_mutations"], 0)
        self.assertTrue(all("core." not in sql and "token_attempt" not in sql for sql, _ in self.connection.calls))
        self.assertIn("fungip.catalog.import", self.connection.calls[1][0])
