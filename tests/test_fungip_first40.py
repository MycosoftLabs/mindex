"""Authored synthetic first-40 controls; no attached dataset or runtime qualification.

All mint/signature values are constructed from synthetic bytes. These controls
call only pure source adapters when an admitted operator later runs them.
"""
import copy
import csv
import hashlib
from io import StringIO
import json
import unittest
from unittest.mock import patch

from mindex_etl.fungip.catalog import canonical
from mindex_etl.fungip.first40 import (
    build_first40_snapshot, pacific_to_utc, validate_admitted_snapshot, validation_rows,
)
from mindex_etl.fungip.first40_detail import public_first40_launch


ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
AS_OF_PT = "2026-10-02 15:50 PDT"
CORRECTION_TIME = "2026-10-02T23:15:34Z"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
URL_FIELDS = (
    "solana_explorer_url", "solana_explorer_tx_url", "solscan_tx_url", "solscan_token_url",
    "usepaid_url", "usepaid_short_url", "pumpfun_url", "metadata_uri",
)
FG026_NULL_FIELDS = (
    "launch_tx", "launched_at", "launched_at_pt", "solana_explorer_tx_url", "solscan_tx_url",
    "solscan_token_url", "usepaid_short_url", "token_program",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded_identifier(number, size):
    raw = number.to_bytes(size, "big")
    remaining = int.from_bytes(raw, "big")
    encoded = ""
    while remaining:
        remaining, digit = divmod(remaining, 58)
        encoded = ALPHABET[digit] + encoded
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + encoded


def mint(number):
    return encoded_identifier(number, 32)


def signature(number):
    return encoded_identifier(number, 64)


def csv_bytes(rows):
    headers = list(dict.fromkeys(key for row in rows for key in row))
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def change_launch_mint(row, value):
    """Keep supplied synthetic URL cells consistent while changing one identity."""
    row["mint"] = value
    row["explorer_mint_url"] = "https://explorer.solana.com/address/" + value
    row["solscan_token_url"] = "https://solscan.io/token/" + value
    row["usepaid_url"] = "https://usepaid.app/token/" + value
    row["pumpfun_url"] = "https://pump.fun/coin/" + value


def change_snapshot_mint(row, value):
    row["mint_address"] = value
    row["solana_explorer_url"] = "https://explorer.solana.com/address/" + value
    row["solscan_token_url"] = "https://solscan.io/token/" + value
    row["usepaid_url"] = "https://usepaid.app/token/" + value
    row["pumpfun_url"] = "https://pump.fun/coin/" + value


def reseal(snapshot, refresh_report=True):
    snapshot = copy.deepcopy(snapshot)
    snapshot.pop("snapshot_sha256", None)
    if refresh_report:
        # Deeper negative controls must reach the record invariant, rather than
        # failing solely because the derived report still describes the old row.
        snapshot["validation_report"] = validation_rows(snapshot["records"])
    snapshot["snapshot_sha256"] = sha(canonical(snapshot).encode("utf-8"))
    return snapshot


class SyntheticSources:
    """300 science rows, 39 prior canonical rows and five excluded observations."""

    def __init__(self):
        self.catalog = []
        for index in range(1, 301):
            sid = f"FG{index:03d}"
            name = f"Synthetic species {index:03d}"
            requested = name
            if sid == "FG033":
                name = requested = "Ganoderma lucidum"
            elif sid == "FG034":
                name, requested = "Ganoderma sichuanense", "Ganoderma lingzhi"
            self.catalog.append({
                "species_id": sid, "ticker": f"FIX{index:03d}", "accepted_name": name,
                "requested_name": requested, "common_name": f"Synthetic common name {index:03d}",
                "dna_sha256": sha(("synthetic-reference-" + sid).encode()),
                "dna_accession_version": f"SYN{index:06d}.1", "dna_database": "Synthetic database",
                "dna_source_url": "https://example.test/accessions/" + sid,
                "image_credit": "Synthetic photographer", "image_license": "CC BY 4.0",
                "image_sha256": sha(("synthetic-image-" + sid).encode()),
                "launch_blockers": json.dumps(["synthetic_catalog_review_open"]),
            })
        self.science = {row["species_id"]: row for row in self.catalog}
        self.launch = [self.launch_row(index) for index in range(1, 41) if index != 26]
        self.excluded = []
        for number, sid, status in (
                (101, "FG001", "superseded_bad_hash"),
                (102, "FG002", "superseded_bad_hash"),
                (103, "FG003", "superseded_bad_hash"),
                (121, "FG021", "superseded_duplicate"),
                (126, "FG026", "live_but_invalid_needs_reissue")):
            row = self.launch_row(int(sid[2:]), number)
            row["record_status"] = status
            row["needs_reissue"] = "True" if sid == "FG026" else "False"
            if status != "superseded_duplicate":
                row["description_hash"] = sha(("synthetic-rejected-description-" + sid).encode())
                row["onchain_description"] = "SHA-256 " + row["description_hash"] + " / Fees to @nodefather via UsePaid"
                row["hash_match"] = "False"
            self.excluded.append(row)
        self.launch.extend(self.excluded)
        for row in self.launch:
            own = [other["mint"] for other in self.excluded if other["species_id"] == row["species_id"]]
            row["superseded_mints"] = ";".join(own) if row["record_status"] == "canonical" else ""
        replacement = mint(226)
        old21 = next(row for row in self.excluded if row["species_id"] == "FG021")
        old26 = next(row for row in self.excluded if row["species_id"] == "FG026")
        current21 = self.canonical_row("FG021")
        self.correction = {
            "schema_version": 1, "record_type": "DIRECT_USER_CORRECTION_AND_INPUT_SELECTION",
            "scope": ["FG001", "FG040"], "source_designation": {},
            "owner_entity": "MycoDAO", "recipient": "@nodefather",
            "verification_semantics": {
                "requested_launch_status": "verified",
                "evidence_origin": "Synthetic user-supplied verification fixture",
                "independent_chain_verification_performed_by_this_chat": False,
                "do_not_promote_to": "Independent chain verification or canonical taxonomy UUID",
            },
            "corrections": [
                {
                    "species_id": "FG026", "ticker": self.science["FG026"]["ticker"],
                    "accepted_name": self.science["FG026"]["accepted_name"],
                    "mint_address": replacement,
                    "usepaid_url": "https://usepaid.app/token/" + replacement,
                    "pumpfun_url": "https://pump.fun/coin/" + replacement,
                    "solana_explorer_url": "https://explorer.solana.com/address/" + replacement,
                    "metadata_uri": "https://usepaid.app/m/synthetic-fg026-reissue",
                    "on_chain_description": "SHA-256 " + self.science["FG026"]["dna_sha256"],
                    "dna_sha256": self.science["FG026"]["dna_sha256"],
                    "dna_accession_version": self.science["FG026"]["dna_accession_version"],
                    "decimals": 6, "supply_display": 1000000000,
                    "user_verified_authorities_revoked": ["mint", "freeze", "update"],
                    "launch_tx": None, "launched_at_pt": None,
                    "solana_explorer_tx_url": None, "solscan_tx_url": None,
                    "superseded_mints": [old26["mint"]], "needs_reissue": False,
                    "launch_status": "verified",
                    "forbidden_inheritance": "Unspecified reissue fields remain null; never copy the superseded observation.",
                },
                {
                    "species_id": "FG021", "ticker": self.science["FG021"]["ticker"],
                    "accepted_name": self.science["FG021"]["accepted_name"],
                    "mint_address": current21["mint"], "launch_tx": current21["launch_tx"],
                    "launched_at_pt": current21["launched_at_pt"],
                    "superseded_mints": [old21["mint"]],
                    "canonical_pending_confirmation": False, "launch_status": "verified",
                    "url_rule": "Retain literal source cells; do not derive URLs.",
                },
            ],
            "expected": {"canonical_species_count": 40, "superseded_mint_count": 5,
                         "FG041_FG300_launch_changes": 0},
            "remaining_review_flags": "Preserve source science reviews and null reissue gaps.",
            "boundaries": {"wallet_actions": False, "production_writes_by_root": False,
                           "production_writer": "Cursor",
                           "database_apply_requires": "Exact reviewed source pins and operator execution",
                           "no_financial_entries_for": ["Mycosoft Inc", "Mycosoft LLC"]},
            "recorded_at_utc": CORRECTION_TIME,
        }
        self.handoff_override = None

    def canonical_row(self, sid):
        return next(row for row in self.launch if row["species_id"] == sid and row["record_status"] == "canonical")

    def launch_row(self, index, identifier=None):
        sid = f"FG{index:03d}"
        science = self.science[sid]
        number = identifier if identifier is not None else index
        value, tx = mint(number), signature(number)
        return {
            "species_id": sid, "ticker": science["ticker"], "accepted_name": science["accepted_name"],
            "mint": value, "launch_tx": tx, "launched_at_pt": "2026-10-02 15:16:19 PT",
            "explorer_mint_url": "https://explorer.solana.com/address/" + value,
            "explorer_tx_url": "https://explorer.solana.com/tx/" + tx,
            "solscan_tx_url": "https://solscan.io/tx/" + tx,
            "solscan_token_url": "https://solscan.io/token/" + value,
            "usepaid_url": "https://usepaid.app/token/" + value,
            "website_short_url": f"https://usepaid.app/t/Synthetic{number}",
            "pumpfun_url": "https://pump.fun/coin/" + value,
            "metadata_uri": f"https://usepaid.app/m/synthetic-{number}",
            "recipient": "@nodefather", "token_program": TOKEN_2022, "decimals": "6",
            "supply_raw": "1000000000000000", "record_status": "canonical",
            "needs_reissue": "False", "hash_match": "True", "superseded_mints": "",
            "dna_accession": science["dna_accession_version"],
            "catalog_dna_sha256": science["dna_sha256"], "catalog_image_sha256": science["image_sha256"],
            "description_hash": science["dna_sha256"], "onchain_name": science["accepted_name"],
            "onchain_symbol": science["ticker"],
            "onchain_description": "SHA-256 " + science["dna_sha256"] + " / Fees to @nodefather via UsePaid",
            "name_match": "True", "symbol_match": "True",
        }

    def handoff(self):
        if self.handoff_override is not None:
            return self.handoff_override
        lines = ["# Synthetic first-40 handoff", ""]
        labels = (("Explorer tx", "explorer_tx_url"), ("Solscan tx", "solscan_tx_url"),
                  ("Explorer mint", "explorer_mint_url"), ("UsePaid", "usepaid_url"),
                  ("pump.fun", "pumpfun_url"), ("Metadata URI", "metadata_uri"))
        for index in range(1, 41):
            sid = f"FG{index:03d}"
            science = self.science[sid]
            lines.extend([f'### {sid} · {science["ticker"]} · *{science["accepted_name"]}*',
                          f'- DNA accession: `{science["dna_accession_version"]}`; dna_sha256: `{science["dna_sha256"]}`'])
            if sid == "FG026":
                row = next(row for row in self.excluded if row["species_id"] == sid)
                lines.extend(["**NO VALID MINT, needs_reissue=true**",
                              f'- Live but invalid mint: `{row["mint"]}` (launch tx `{row["launch_tx"]}`, {row["launched_at_pt"]})'])
            else:
                row = self.canonical_row(sid)
                lines.extend(["**CANONICAL**, verification: VERIFIED", f'- Mint: `{row["mint"]}`',
                              f'- Launch tx: `{row["launch_tx"]}` ({row["launched_at_pt"]})'])
                for label, key in labels:
                    if row[key].strip():
                        suffix = f' (short link: {row["website_short_url"]})' if label == "UsePaid" and row["website_short_url"].strip() else ""
                        lines.append(f"- {label}: {row[key]}" + suffix)
            lines.append("")
        lines.extend(["## Superseded / do-not-use mints", "",
                      "| Species | Ticker | Status | Mint | Transaction | Reason |",
                      "| --- | --- | --- | --- | --- | --- |"])
        for row in self.excluded:
            lines.append(f'| {row["species_id"]} | {row["ticker"]} | {row["record_status"]} | `{row["mint"]}` | `{row["launch_tx"]}` | synthetic excluded observation |')
        return ("\n".join(lines) + "\n").encode("utf-8")

    def arguments(self, dialect="first40_launch_log_v1", encoding="semicolon"):
        rows = copy.deepcopy(self.launch)
        if encoding == "json":
            for row in rows:
                row["superseded_mints"] = json.dumps(row["superseded_mints"].split(";") if row["superseded_mints"] else [])
        if dialect == "launch_ledger_v1":
            names = {"accepted_name": "name", "mint": "mint_address", "explorer_mint_url": "explorer_url",
                     "website_short_url": "usepaid_short_url"}
            rows = [{names.get(key, key): value for key, value in row.items()} for row in rows]
        catalog_raw, launch_raw, handoff_raw = csv_bytes(self.catalog), csv_bytes(rows), self.handoff()
        correction = copy.deepcopy(self.correction)
        correction["source_designation"] = {
            "catalog": "synthetic/catalog.csv", "catalog_sha256": sha(catalog_raw),
            "launch_log": "synthetic/launch.csv", "launch_log_sha256": sha(launch_raw),
            "handoff": "synthetic/handoff.md", "handoff_sha256": sha(handoff_raw),
            "rationale": "Synthetic source selection for offline authored controls",
            "precedence": "Only explicit FG021 and FG026 corrections override historical source facts",
        }
        correction_raw = json.dumps(correction, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        bindings = {"catalog_sha256": sha(catalog_raw), "launch_sha256": sha(launch_raw),
                    "handoff_sha256": sha(handoff_raw), "correction_sha256": sha(correction_raw)}
        authority = {"approved": True, "approval_reference": "synthetic-direct-user-approval",
                     "source_bindings": bindings, "launch_schema": dialect,
                     "superseded_encoding": encoding, "correction_schema": "direct_user_correction_v1"}
        return [catalog_raw, launch_raw, handoff_raw, correction_raw, authority, AS_OF_PT]


def record(snapshot, sid):
    return next(row for row in snapshot["records"] if row["species_id"] == sid)


def public_arguments(snapshot, sid="FG021"):
    row = copy.deepcopy(record(snapshot, sid))
    for key in ("source_reported_as_of_pt", "source_reported_as_of_utc", "correction_recorded_at_utc"):
        row[key] = snapshot[key]
    parent = {"species_id": sid, "accepted_name": row["accepted_name"],
              "image_valid": True, "sequence_valid": True,
              "record": {"accepted_name": row["accepted_name"], "ticker": row["ticker"],
                         "dna": {"sha256": row["dna_sha256"], "accession_version": row["dna_accession_version"]},
                         "image": {"attribution": row["image_credit"], "license_code": row["image_license"], "sha256": row["image_sha256"]}}}
    return row, parent


class First40SourceTests(unittest.TestCase):
    def assert_source_hold(self, arguments):
        try:
            snapshot = build_first40_snapshot(*arguments)
        except ValueError:
            return
        self.assertIsNot(snapshot.get("admissible"), True)
        with self.assertRaises(ValueError):
            validate_admitted_snapshot(snapshot)

    def test_authority_and_explicit_formats_are_checked_before_csv_or_handoff_parsing(self):
        raw = [b"malformed synthetic catalog", b"malformed synthetic launch", b"malformed synthetic handoff", b"malformed synthetic correction"]
        bindings = dict(zip(("catalog_sha256", "launch_sha256", "handoff_sha256", "correction_sha256"), map(sha, raw)))
        approved = {"approved": True, "approval_reference": "synthetic-approval", "source_bindings": bindings,
                    "launch_schema": "first40_launch_log_v1", "superseded_encoding": "semicolon",
                    "correction_schema": "direct_user_correction_v1"}
        invalid = [{**approved, "approved": False}, {**approved, "approved": "true"},
                   {**approved, "approval_reference": ""}, {**approved, "source_bindings": {}},
                   {**approved, "superseded_encoding": "comma"},
                   {**approved, "correction_schema": "autodetect"}]
        for key in ("launch_schema", "superseded_encoding", "correction_schema"):
            missing = copy.deepcopy(approved)
            missing.pop(key)
            invalid.append(missing)
        for key in bindings:
            altered = copy.deepcopy(approved)
            altered["source_bindings"][key] = "f" * 64
            invalid.append(altered)
            missing = copy.deepcopy(approved)
            missing["source_bindings"].pop(key)
            invalid.append(missing)
        for authority in invalid:
            with self.subTest(authority=authority):
                with patch("mindex_etl.fungip.first40.csv_rows") as csv_parser, patch("mindex_etl.fungip.first40.handoff_records") as handoff_parser, patch("mindex_etl.fungip.first40.correction_records") as correction_parser:
                    with self.assertRaises(ValueError):
                        build_first40_snapshot(*raw, authority, AS_OF_PT)
                    csv_parser.assert_not_called()
                    handoff_parser.assert_not_called()
                    correction_parser.assert_not_called()

    def test_designated_first40_dialect_and_both_excluded_encodings_are_supported(self):
        for encoding in ("semicolon", "json"):
            with self.subTest(encoding=encoding):
                snapshot = build_first40_snapshot(*SyntheticSources().arguments(encoding=encoding))
                self.assertEqual(len(snapshot["records"]), 40)
                self.assertEqual(snapshot["source_bindings"], snapshot["authority"]["source_bindings"])
                self.assertEqual(snapshot["corrected_input_version"], "direct_user_correction_v1")
                self.assertEqual(validate_admitted_snapshot(snapshot), snapshot)

    def test_declared_legacy_dialect_is_denied_before_parsing_matching_legacy_headers(self):
        arguments = SyntheticSources().arguments("launch_ledger_v1")
        with patch("mindex_etl.fungip.first40.csv_rows") as csv_parser, patch("mindex_etl.fungip.first40.handoff_records") as handoff_parser:
            with self.assertRaises(ValueError):
                build_first40_snapshot(*arguments)
            csv_parser.assert_not_called()
            handoff_parser.assert_not_called()

    def test_literal_double_separator_description_is_supported_without_rewriting_cells(self):
        fixture = SyntheticSources()
        row = fixture.canonical_row("FG004")
        row["onchain_description"] = row["onchain_description"].replace(" / Fees", " /  / Fees")
        snapshot = build_first40_snapshot(*fixture.arguments())
        self.assertIs(snapshot["admissible"], True)
        self.assertIs(record(snapshot, "FG004")["source_hash_match"], True)
        self.assertIn(" /  / Fees", row["onchain_description"])

    def test_unevidenced_description_separators_stop_admission(self):
        for separator in (" / / ", " // ", " /  /  / ", " - "):
            fixture = SyntheticSources()
            row = fixture.canonical_row("FG004")
            row["onchain_description"] = "SHA-256 " + row["description_hash"] + separator + "Fees to @nodefather via UsePaid"
            with self.subTest(separator=separator):
                self.assert_source_hold(fixture.arguments())

    def test_all_forty_are_approved_source_verified_without_new_chain_verification(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        self.assertEqual({row["species_id"] for row in snapshot["records"]}, {f"FG{i:03d}" for i in range(1, 41)})
        for row in snapshot["records"]:
            with self.subTest(species_id=row["species_id"]):
                self.assertEqual(row["launch_status"], "verified")
                self.assertIs(row["canonical_approved"], True)
                self.assertIs(row["source_hash_match"], True)
                self.assertIsNone(row["candidate_launch"])
                self.assertEqual(row["verification_basis"], "user_supplied_and_attached_verification")
                self.assertIs(row["new_chain_verified"], False)
                self.assertEqual(row["owner_entity"], "MycoDAO")
                self.assertIn("synthetic_catalog_review_open", row["catalog_review_flags"])
        self.assertEqual(snapshot["source_reported_as_of_pt"], AS_OF_PT)
        self.assertEqual(snapshot["source_reported_as_of_utc"], "2026-10-02T22:50:00Z")
        self.assertEqual(snapshot["correction_recorded_at_utc"], CORRECTION_TIME)

    def test_fg021_direct_choice_is_canonical_and_not_a_pending_candidate(self):
        fixture = SyntheticSources()
        snapshot = build_first40_snapshot(*fixture.arguments())
        selected = record(snapshot, "FG021")
        self.assertEqual(selected["mint_address"], fixture.canonical_row("FG021")["mint"])
        self.assertEqual(selected["launch_tx"], fixture.canonical_row("FG021")["launch_tx"])
        self.assertIs(selected["canonical_approved"], True)
        self.assertIsNone(selected["candidate_launch"])
        self.assertIs(selected["new_chain_verified"], False)
        self.assertNotIn("canonical_selection_requires_Morgan_confirmation", selected["gap_flags"])
        superseded = next(row for row in fixture.excluded if row["species_id"] == "FG021")
        change_launch_mint(fixture.canonical_row("FG021"), superseded["mint"])
        self.assert_source_hold(fixture.arguments())

    def test_fg026_reissue_uses_only_supplied_new_fields_and_preserves_old_mint_as_excluded(self):
        fixture = SyntheticSources()
        snapshot = build_first40_snapshot(*fixture.arguments())
        selected = record(snapshot, "FG026")
        correction = fixture.correction["corrections"][0]
        old = next(row for row in fixture.excluded if row["species_id"] == "FG026")
        self.assertEqual(selected["mint_address"], correction["mint_address"])
        self.assertEqual(selected["superseded_mints"], [old["mint"]])
        for key in ("usepaid_url", "pumpfun_url", "solana_explorer_url", "metadata_uri"):
            self.assertEqual(selected[key], correction[key])
        for key in FG026_NULL_FIELDS:
            with self.subTest(field=key):
                self.assertIsNone(selected[key])
                self.assertIn("blank_" + key, selected["gap_flags"])
        self.assertEqual(selected["launch_status"], "verified")
        self.assertIs(selected["source_hash_match"], True)
        self.assertFalse(any("needs_reissue" in flag for flag in selected["gap_flags"]))
        self.assertNotIn(old["launch_tx"], json.dumps(selected))
        self.assertNotIn(old["metadata_uri"], json.dumps(selected))

    def test_fg026_inherited_transaction_or_timestamp_is_rejected_even_after_resealing(self):
        fixture = SyntheticSources()
        snapshot = build_first40_snapshot(*fixture.arguments())
        old = next(row for row in fixture.excluded if row["species_id"] == "FG026")
        changes = ({"launch_tx": old["launch_tx"]},
                   {"launched_at_pt": old["launched_at_pt"], "launched_at": pacific_to_utc(old["launched_at_pt"])},
                   {"token_program": old["token_program"]},
                   {"launch_tx": old["launch_tx"], "solana_explorer_tx_url": old["explorer_tx_url"]})
        for changed in changes:
            with self.subTest(changed=changed):
                altered = copy.deepcopy(snapshot)
                record(altered, "FG026").update(changed)
                with self.assertRaises(ValueError):
                    validate_admitted_snapshot(reseal(altered))

    def test_corrected_snapshot_rejects_unresolved_or_new_chain_promoted_states(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        for state in ("gap", "needs_reissue", "canonical_pending_confirmation", "source_reported_canonical"):
            altered = copy.deepcopy(snapshot)
            record(altered, "FG021")["launch_status"] = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate_admitted_snapshot(reseal(altered))
        altered = copy.deepcopy(snapshot)
        record(altered, "FG021")["new_chain_verified"] = True
        with self.assertRaises(ValueError):
            validate_admitted_snapshot(reseal(altered))

    def test_duplicate_canonical_selected_mint_and_out_of_scope_launches_are_held(self):
        duplicate = SyntheticSources()
        duplicate.launch.append(copy.deepcopy(duplicate.canonical_row("FG004")))
        self.assert_source_hold(duplicate.arguments())
        outside = SyntheticSources()
        row = copy.deepcopy(outside.canonical_row("FG004"))
        row["species_id"] = "FG041"
        outside.launch.append(row)
        self.assert_source_hold(outside.arguments())
        outside_table = SyntheticSources()
        outside_table.handoff_override = outside_table.handoff() + (
            f'| FG041 | {outside_table.science["FG041"]["ticker"]} | superseded_duplicate | '
            f'`{mint(444)}` | `{signature(444)}` | synthetic out-of-scope exclusion |\n'
        ).encode("utf-8")
        self.assert_source_hold(outside_table.arguments())
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        change_snapshot_mint(record(snapshot, "FG002"), record(snapshot, "FG001")["mint_address"])
        with self.assertRaises(ValueError):
            validate_admitted_snapshot(reseal(snapshot))

    def test_superseded_mint_cannot_be_selected_for_any_species(self):
        fixture = SyntheticSources()
        snapshot = build_first40_snapshot(*fixture.arguments())
        change_snapshot_mint(record(snapshot, "FG004"), fixture.excluded[0]["mint"])
        with self.assertRaises(ValueError):
            validate_admitted_snapshot(reseal(snapshot))

    def test_catalog_hash_name_and_handoff_identity_disagreements_stop_admission(self):
        for field in ("catalog_dna_sha256", "catalog_image_sha256", "onchain_name"):
            fixture = SyntheticSources()
            fixture.canonical_row("FG004")[field] = "f" * 64 if field.endswith("sha256") else "Different synthetic species"
            with self.subTest(field=field):
                self.assert_source_hold(fixture.arguments())
        fixture = SyntheticSources()
        fixture.handoff_override = fixture.handoff().replace(fixture.science["FG004"]["dna_sha256"].encode(), b"f" * 64, 1)
        self.assert_source_hold(fixture.arguments())
        fixture = SyntheticSources()
        fixture.handoff_override = fixture.handoff().replace(b"*Synthetic species 004*", b"*Different handoff species*", 1)
        self.assert_source_hold(fixture.arguments())
        for label in ("DNA accession", "Mint", "Launch tx"):
            fixture = SyntheticSources()
            science, row = fixture.science["FG004"], fixture.canonical_row("FG004")
            pairs = {
                "DNA accession": (
                    f'- DNA accession: `{science["dna_accession_version"]}`; dna_sha256: `{science["dna_sha256"]}`',
                    '- DNA accession: `OTHER.1`; dna_sha256: `' + "f" * 64 + '`'),
                "Mint": (f'- Mint: `{row["mint"]}`', f'- Mint: `{mint(444)}`'),
                "Launch tx": (
                    f'- Launch tx: `{row["launch_tx"]}` ({row["launched_at_pt"]})',
                    f'- Launch tx: `{signature(444)}` ({row["launched_at_pt"]})'),
            }
            original, conflicting = pairs[label]
            raw = fixture.handoff()
            self.assertIn(original.encode("utf-8"), raw)
            fixture.handoff_override = raw.replace(
                original.encode("utf-8"), (original + "\n" + conflicting).encode("utf-8"), 1)
            with self.subTest(repeated_label=label):
                self.assert_source_hold(fixture.arguments())

    def test_direct_correction_identity_scope_and_duplicates_are_not_silent_overrides(self):
        for field, value in (("ticker", "DIFFERENT"), ("accepted_name", "Different correction species"),
                             ("dna_sha256", "f" * 64), ("dna_accession_version", "OTHER.1")):
            fixture = SyntheticSources()
            fixture.correction["corrections"][0][field] = value
            with self.subTest(field=field):
                self.assert_source_hold(fixture.arguments())
        fixture = SyntheticSources()
        fixture.correction["corrections"].append(copy.deepcopy(fixture.correction["corrections"][1]))
        self.assert_source_hold(fixture.arguments())
        fixture = SyntheticSources()
        extra = copy.deepcopy(fixture.correction["corrections"][1])
        extra["species_id"] = "FG041"
        fixture.correction["corrections"].append(extra)
        self.assert_source_hold(fixture.arguments())

    def test_blank_source_urls_stay_null_flagged_and_are_never_derived(self):
        fixture = SyntheticSources()
        row = fixture.canonical_row("FG004")
        for key in ("website_short_url", "solscan_token_url", "metadata_uri"):
            row[key] = ""
        row["metadata_uri"] = "   "
        snapshot = build_first40_snapshot(*fixture.arguments())
        selected = record(snapshot, "FG004")
        for key in ("usepaid_short_url", "solscan_token_url", "metadata_uri"):
            self.assertIsNone(selected[key])
            self.assertIn("blank_" + key, selected["gap_flags"])
        self.assertEqual(selected["usepaid_url"], row["usepaid_url"])
        self.assertEqual(selected["solana_explorer_tx_url"], row["explorer_tx_url"])
        altered = copy.deepcopy(snapshot)
        record(altered, "FG004")["gap_flags"].remove("blank_metadata_uri")
        with self.assertRaises(ValueError):
            validate_admitted_snapshot(reseal(altered))

    def test_nonstring_or_nonliteral_supplied_urls_are_rejected(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        for value in (True, 7, [], {}, " https://usepaid.app/t/Synthetic4", "http://usepaid.app/t/Synthetic4",
                      "https://user:pass@usepaid.app/t/Synthetic4"):
            altered = copy.deepcopy(snapshot)
            record(altered, "FG004")["usepaid_short_url"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_admitted_snapshot(reseal(altered))

    def test_hash_and_boolean_cells_cannot_promote_unqualified_source(self):
        for value in ("False", "yes", "1"):
            fixture = SyntheticSources()
            fixture.canonical_row("FG004")["hash_match"] = value
            with self.subTest(value=value):
                self.assert_source_hold(fixture.arguments())
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        altered = copy.deepcopy(snapshot)
        record(altered, "FG004")["source_hash_match"] = "true"
        with self.assertRaises(ValueError):
            validate_admitted_snapshot(reseal(altered))

    def test_declared_legacy_not_issued_rows_are_rejected_without_header_autodetection(self):
        fixture = SyntheticSources()
        fixture.handoff_override = fixture.handoff()
        for row in fixture.launch:
            row["record_status"] = "NOT_ISSUED"
        self.assert_source_hold(fixture.arguments())
        self.assert_source_hold(fixture.arguments("launch_ledger_v1"))
        arguments = SyntheticSources().arguments()
        arguments[4]["launch_schema"] = "launch_ledger_v1"
        self.assert_source_hold(arguments)

    def test_fg034_lingzhi_synonym_does_not_merge_fg033_lucidum(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        lucidum, sichuanense = record(snapshot, "FG033"), record(snapshot, "FG034")
        self.assertEqual(lucidum["accepted_name"], "Ganoderma lucidum")
        self.assertEqual(lucidum["synonyms"], [])
        self.assertEqual(sichuanense["accepted_name"], "Ganoderma sichuanense")
        self.assertEqual(sichuanense["synonyms"], ["Ganoderma lingzhi"])
        self.assertNotEqual(lucidum["mint_address"], sichuanense["mint_address"])
        self.assertNotEqual(lucidum["dna_sha256"], sichuanense["dna_sha256"])

    def test_resealed_taxonomic_guards_and_exact_schema_version_types_remain_required(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        for sid, wrong_name in (("FG033", "Ganoderma sichuanense"),
                                ("FG034", "Ganoderma lucidum")):
            altered = copy.deepcopy(snapshot)
            record(altered, sid)["accepted_name"] = wrong_name
            with self.subTest(species_id=sid), self.assertRaises(ValueError):
                validate_admitted_snapshot(reseal(altered))
        altered = copy.deepcopy(snapshot)
        record(altered, "FG034")["catalog_review_flags"].remove(
            "lingzhi_sichuanense_taxonomic_concept_review_open")
        with self.assertRaises(ValueError):
            validate_admitted_snapshot(reseal(altered))
        for value in (True, 1.0):
            fixture = SyntheticSources()
            fixture.correction["schema_version"] = value
            with self.subTest(source="correction", value=value):
                self.assert_source_hold(fixture.arguments())
            altered = copy.deepcopy(snapshot)
            altered["schema_version"] = value
            with self.subTest(source="snapshot", value=value), self.assertRaises(ValueError):
                validate_admitted_snapshot(reseal(altered))

    def test_prepared_snapshot_hash_is_immutable_and_validation_detaches(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        detached = validate_admitted_snapshot(snapshot)
        self.assertEqual(detached, snapshot)
        self.assertIsNot(detached, snapshot)
        self.assertIsNot(detached["records"], snapshot["records"])
        self.assertIsNot(detached["records"][0], snapshot["records"][0])
        detached["records"][0]["catalog_review_flags"].append("detached-only")
        self.assertNotIn("detached-only", snapshot["records"][0]["catalog_review_flags"])
        altered = copy.deepcopy(snapshot)
        altered["records"][0]["ticker"] = "ALTERED"
        with self.assertRaises(ValueError):
            validate_admitted_snapshot(altered)

    def test_resealed_derived_report_tampering_does_not_create_import_or_chain_evidence(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        for key, value in (("written", True), ("new_chain_verified", True),
                           ("written", 0), ("written", 0.0),
                           ("new_chain_verified", 0), ("new_chain_verified", 0.0),
                           ("canonical_approved", 1), ("canonical_approved", 1.0),
                           ("source_hash_match", 1), ("source_hash_match", 1.0)):
            altered = copy.deepcopy(snapshot)
            altered["validation_report"][0][key] = value
            with self.subTest(field=key, value=value), self.assertRaises(ValueError):
                validate_admitted_snapshot(reseal(altered, refresh_report=False))


class PacificTimestampTests(unittest.TestCase):
    def test_supplied_minute_precision_is_preserved_while_utc_uses_seconds(self):
        self.assertEqual(pacific_to_utc("2026-10-02 15:50 PDT"), "2026-10-02T22:50:00Z")
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        self.assertEqual(snapshot["source_reported_as_of_pt"], "2026-10-02 15:50 PDT")
        self.assertNotEqual(snapshot["source_reported_as_of_pt"], "2026-10-02 15:50:00 PT")
        self.assertEqual(snapshot["source_reported_as_of_utc"], "2026-10-02T22:50:00Z")

    def test_unique_pt_and_explicit_fall_offsets_are_exact(self):
        self.assertEqual(pacific_to_utc("2026-10-02 15:16:19 PT"), "2026-10-02T22:16:19Z")
        self.assertEqual(pacific_to_utc("2026-11-01 01:30:00 PDT"), "2026-11-01T08:30:00Z")
        self.assertEqual(pacific_to_utc("2026-11-01 01:30:00 PST"), "2026-11-01T09:30:00Z")

    def test_ambiguous_nonexistent_or_incorrectly_labeled_pt_is_rejected(self):
        for value in ("2026-11-01 01:30:00 PT", "2026-03-08 02:30:00 PT",
                      "2026-11-01 01:30 PT", "2026-03-08 02:30 PT",
                      "2026-03-08 02:30:00 PDT", "2026-03-08 02:30:00 PST",
                      "2026-01-15 12:00:00 PDT", "2026-07-15 12:00:00 PST", "15:16:19 PT"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pacific_to_utc(value)


class First40PublicProjectionTests(unittest.TestCase):
    def test_absent_association_is_empty_without_fabrication(self):
        self.assertIsNone(public_first40_launch(None, {"species_id": "FG021"}))

    def test_public_projection_preserves_qualification_and_omits_private_and_financial_fields(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        row, parent = public_arguments(snapshot)
        row.update(authority={"source_paths": "PRIVATE_SYNTHETIC_PATH"},
                   operator_public_wallet="PRIVATE_SYNTHETIC_WALLET", recipient_identity={"email": "PRIVATE_SYNTHETIC_EMAIL"},
                   finalized_evidence={"raw_rpc": "PRIVATE_SYNTHETIC_RPC"},
                   finance={"entity": "Mycosoft Inc", "fee_entitlement": "PRIVATE_SYNTHETIC_FINANCE"})
        projection = public_first40_launch(row, parent)
        self.assertEqual(projection["launch_status"], "verified")
        self.assertEqual(projection["verification_basis"], "user_supplied_and_attached_verification")
        self.assertIs(projection["new_chain_verified"], False)
        self.assertEqual(projection["owner_entity"], "MycoDAO")
        self.assertEqual(projection["source_reported_as_of_pt"], AS_OF_PT)
        self.assertEqual(projection["correction_recorded_at_utc"], CORRECTION_TIME)
        serialized = json.dumps(projection)
        self.assertNotIn("PRIVATE_SYNTHETIC", serialized)
        for key in ("authority", "operator_public_wallet", "recipient_identity", "finalized_evidence", "finance", "source_paths"):
            self.assertNotIn(key, projection)

    def test_parent_scientific_identity_and_image_disagreements_cannot_be_projected(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        for target, field, value in (("record", "accepted_name", "Other synthetic species"),
                                     ("record", "ticker", "OTHER"),
                                     ("dna", "sha256", "f" * 64), ("dna", "accession_version", "OTHER.1"),
                                     ("image", "sha256", "f" * 64), ("image", "license_code", "Different license"),
                                     ("image", "attribution", "Different photographer")):
            row, parent = public_arguments(snapshot)
            container = parent["record"] if target == "record" else parent["record"][target]
            container[field] = value
            with self.subTest(target=target, field=field), self.assertRaises(ValueError):
                public_first40_launch(row, parent)

    def test_conflicting_top_level_parent_name_with_unchanged_nested_identity_is_held(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        row, parent = public_arguments(snapshot)
        original_row, original_parent = copy.deepcopy(row), copy.deepcopy(parent)
        conflicting_parent = copy.deepcopy(parent)
        conflicting_parent["accepted_name"] = "Conflicting top-level synthetic species"
        configured_parent = copy.deepcopy(conflicting_parent)
        self.assertEqual(conflicting_parent["record"], original_parent["record"])
        self.assertEqual(conflicting_parent["record"]["accepted_name"], row["accepted_name"])
        self.assertEqual(conflicting_parent["record"]["dna"]["sha256"], row["dna_sha256"])
        with self.assertRaises(ValueError):
            public_first40_launch(row, conflicting_parent)
        self.assertEqual(row, original_row)
        self.assertEqual(parent, original_parent)
        self.assertEqual(conflicting_parent, configured_parent)

    def test_missing_top_level_parent_name_is_held_without_mutating_input_fixtures(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        row, parent = public_arguments(snapshot)
        original_row, original_parent = copy.deepcopy(row), copy.deepcopy(parent)
        missing_parent = copy.deepcopy(parent)
        missing_parent.pop("accepted_name")
        configured_parent = copy.deepcopy(missing_parent)
        self.assertEqual(missing_parent["record"], original_parent["record"])
        self.assertEqual(missing_parent["record"]["accepted_name"], row["accepted_name"])
        self.assertEqual(missing_parent["record"]["dna"]["sha256"], row["dna_sha256"])
        with self.assertRaises(ValueError):
            public_first40_launch(row, missing_parent)
        self.assertEqual(row, original_row)
        self.assertEqual(parent, original_parent)
        self.assertEqual(missing_parent, configured_parent)

    def test_malformed_association_or_parent_is_an_explicit_projection_hold(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        row, parent = public_arguments(snapshot)
        missing = copy.deepcopy(row)
        missing.pop("mint_address")
        for malformed in ({}, missing, []):
            with self.subTest(row=malformed), self.assertRaises(ValueError):
                public_first40_launch(malformed, parent)
        for malformed in ({"species_id": "FG021"}, {**parent, "record": []}):
            with self.subTest(parent=malformed), self.assertRaises(ValueError):
                public_first40_launch(row, malformed)

    def test_invalid_parent_reference_or_image_cannot_be_projected_as_available(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        for key in ("image_valid", "sequence_valid"):
            row, parent = public_arguments(snapshot)
            parent[key] = False
            with self.subTest(flag=key), self.assertRaises(ValueError):
                public_first40_launch(row, parent)

    def test_nonverified_or_promoted_public_association_is_rejected(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        for changes in ({"launch_status": "canonical_pending_confirmation"}, {"canonical_approved": False},
                        {"source_hash_match": False}, {"new_chain_verified": True}):
            row, parent = public_arguments(snapshot)
            row.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                public_first40_launch(row, parent)

    def test_fg026_null_transaction_gaps_survive_public_projection(self):
        snapshot = build_first40_snapshot(*SyntheticSources().arguments())
        row, parent = public_arguments(snapshot, "FG026")
        projection = public_first40_launch(row, parent)
        for key in FG026_NULL_FIELDS:
            self.assertIsNone(projection[key])
            self.assertIn("blank_" + key, projection["gap_flags"])
        self.assertEqual(projection["launch_status"], "verified")
        self.assertIs(projection["new_chain_verified"], False)


if __name__ == "__main__":
    unittest.main()
