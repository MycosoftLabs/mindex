from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest

from mindex_etl.jobs.import_taxon_publication_evidence import (
    fetch_and_stage_genbank_publication_evidence,
    stage_genbank_publication_evidence,
)
from mindex_etl.sources.publication_evidence import parse_genbank_publication_evidence


def genbank_xml(*, taxids=("5334",), accession="PZ955173.1") -> bytes:
    taxon_quals = "".join(
        f"<GBQualifier><GBQualifier_name>db_xref</GBQualifier_name><GBQualifier_value>taxon:{taxid}</GBQualifier_value></GBQualifier>"
        for taxid in taxids
    )
    return f"""<GBSet><GBSeq>
      <GBSeq_primary-accession>PZ955173</GBSeq_primary-accession>
      <GBSeq_accession-version>{accession}</GBSeq_accession-version>
      <GBSeq_organism>Schizophyllum commune</GBSeq_organism>
      <GBSeq_feature-table><GBFeature><GBFeature_key>source</GBFeature_key><GBFeature_quals>{taxon_quals}</GBFeature_quals></GBFeature></GBSeq_feature-table>
      <GBSeq_references>
        <GBReference><GBReference_reference>1</GBReference_reference>
          <GBReference_title>Integrated Myochemical and Molecular Characterization of Selected Medicinal Mushrooms Using ITS rDNA Sequencing</GBReference_title>
          <GBReference_journal>Trop J Nat Prod Res (2026) In press</GBReference_journal>
        </GBReference>
        <GBReference><GBReference_reference>2</GBReference_reference>
          <GBReference_title>Direct Submission</GBReference_title><GBReference_journal>Submitted (08-SEP-2026)</GBReference_journal>
        </GBReference>
      </GBSeq_references>
    </GBSeq></GBSet>""".encode()


class Cursor:
    def __init__(self, crosswalk_rows, *, fail_on_evidence=False, existing_review_state=None):
        self.crosswalk_rows = crosswalk_rows
        self.fail_on_evidence = fail_on_evidence
        self.existing_review_state = existing_review_state
        self.calls = []
        self.next_row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "FROM core.taxon_external_id" in sql:
            self.next_rows = self.crosswalk_rows
        elif "SELECT id FROM core.publications" in sql:
            self.next_row = ("a" * 32,)
        elif "INSERT INTO bio.publication_taxon_evidence" in sql:
            if self.fail_on_evidence:
                raise RuntimeError("simulated evidence insert failure")
            self.next_row = None if self.existing_review_state else (
                "b" * 32, "candidate_source_attested",
            )
        elif "SELECT evidence_id, evidence_state FROM bio.publication_taxon_evidence" in sql:
            self.next_row = ("b" * 32, self.existing_review_state)

    def fetchall(self):
        return self.next_rows

    def fetchone(self):
        return self.next_row


class Connection:
    def __init__(self, rows, *, fail_on_evidence=False, existing_review_state=None):
        self.cursor_value = Cursor(
            rows, fail_on_evidence=fail_on_evidence, existing_review_state=existing_review_state,
        )
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_parser_pins_exact_accession_taxid_reference_and_source_hash():
    raw = genbank_xml()
    candidates = parse_genbank_publication_evidence(raw)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["accession_version"] == "PZ955173.1"
    assert candidate["organism"] == "Schizophyllum commune"
    assert candidate["source_taxon_ids"] == ["5334"]
    assert candidate["provider_source_record_id"] == "PZ955173.1#reference=1"
    assert candidate["title"].startswith("Integrated Myochemical")
    assert candidate["evidence_state"] == "candidate_source_attested"
    assert candidate["source_content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert candidate["normalization_sha256"] != candidate["source_content_sha256"]
    assert "Direct Submission" not in candidate["title"]


def test_parser_requires_one_versioned_record_and_rejects_unversioned_accession():
    with pytest.raises(ValueError, match="versioned"):
        parse_genbank_publication_evidence(genbank_xml(accession="PZ955173"))
    with pytest.raises(ValueError, match="exactly one"):
        parse_genbank_publication_evidence(b"<GBSet></GBSet>")


def test_import_stages_exact_crosswalk_candidate_without_promoting_link():
    expected_taxon = UUID("6db28640-67fb-4808-90de-956a856366f7")
    conn = Connection([(expected_taxon, "Schizophyllum commune", "species")])

    result = stage_genbank_publication_evidence(conn, genbank_xml(), expected_accession_version="PZ955173.1")

    assert result == {
        "state": "candidate_source_attested", "accession_version": "PZ955173.1",
        "source_taxon_id": "5334", "taxon_id": str(expected_taxon), "staged": 1,
        "inserted": 1, "existing": 0,
        "evidence_receipts": [{
            "evidence_id": "b" * 32, "provider_source_record_id": "PZ955173.1#reference=1",
            "write_state": "inserted", "review_state": "candidate_source_attested",
        }],
    }
    sql_text = "\n".join(sql for sql, _ in conn.cursor_value.calls)
    assert "INSERT INTO core.publications" in sql_text
    assert "INSERT INTO bio.publication_taxon_evidence" in sql_text
    assert "INSERT INTO bio.publication_taxon (" not in sql_text
    assert "ON CONFLICT (provider, provider_source_record_id, source_content_sha256, taxon_id)" in sql_text
    assert conn.commits == 0


def test_wrong_source_taxid_mapping_is_rejected_before_publication_write():
    splitgill_id = UUID("6db28640-67fb-4808-90de-956a856366f7")
    conn = Connection([(splitgill_id, "Pleurotus ostreatus", "species")])

    result = stage_genbank_publication_evidence(conn, genbank_xml())

    assert result["state"] == "source_name_mismatch"
    assert result["source_ids"] == ["5334"]
    assert not any("INSERT INTO" in sql for sql, _ in conn.cursor_value.calls)


@pytest.mark.parametrize(
    ("rows", "expected_state"),
    [([], "unlinked_exact_external_id"), (
        [("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "Schizophyllum commune", "species"),
         ("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "Schizophyllum commune", "species")],
        "ambiguous_exact_external_id",
    )],
)
def test_missing_or_ambiguous_exact_crosswalk_never_writes(rows, expected_state):
    conn = Connection(rows)
    result = stage_genbank_publication_evidence(conn, genbank_xml())
    assert result["state"] == expected_state
    assert not any("INSERT INTO" in sql for sql, _ in conn.cursor_value.calls)


def test_idempotent_keys_and_caller_owned_rollback_are_explicit():
    crosswalk = [("6db28640-67fb-4808-90de-956a856366f7", "Schizophyllum commune", "species")]
    conn = Connection(crosswalk, fail_on_evidence=True)
    with pytest.raises(RuntimeError, match="simulated evidence insert failure"):
        stage_genbank_publication_evidence(conn, genbank_xml())
    assert conn.commits == 0
    conn.rollback()
    assert conn.rollbacks == 1
    assert any("ON CONFLICT (source, external_id) DO NOTHING" in sql for sql, _ in conn.cursor_value.calls)


def test_fetch_wrapper_uses_exact_accession_and_rejects_response_version_mismatch():
    calls = []

    def fetcher(accession):
        calls.append(accession)
        return genbank_xml(accession="PZ955173.2")

    conn = Connection([])
    result = fetch_and_stage_genbank_publication_evidence(
        conn, "PZ955173.1", expected_source_sha256=hashlib.sha256(genbank_xml(accession="PZ955173.2")).hexdigest(),
        fetcher=fetcher,
    )
    assert calls == ["PZ955173.1"]
    assert result["state"] == "accession_version_mismatch"
    assert result["staged"] == 0


def test_fetch_wrapper_requires_and_enforces_immutable_source_hash_pin():
    raw = genbank_xml()
    conn = Connection([])
    result = fetch_and_stage_genbank_publication_evidence(
        conn, "PZ955173.1", expected_source_sha256="0" * 64, fetcher=lambda _: raw,
    )
    assert result["state"] == "source_hash_mismatch"
    assert result["actual"] == hashlib.sha256(raw).hexdigest()
    assert result["staged"] == 0


def test_empty_citations_still_require_requested_version_and_immutable_hash():
    raw = genbank_xml(accession="PZ955173.2").replace(
        b"Integrated Myochemical and Molecular Characterization of Selected Medicinal Mushrooms Using ITS rDNA Sequencing",
        b"Direct Submission",
    )
    conn = Connection([])
    wrong_hash = fetch_and_stage_genbank_publication_evidence(
        conn, "PZ955173.1", expected_source_sha256="0" * 64, fetcher=lambda _: raw,
    )
    assert wrong_hash["state"] == "source_hash_mismatch"
    wrong_version = fetch_and_stage_genbank_publication_evidence(
        conn, "PZ955173.1", expected_source_sha256=hashlib.sha256(raw).hexdigest(), fetcher=lambda _: raw,
    )
    assert wrong_version["state"] == "accession_version_mismatch"
    assert conn.cursor_value.calls == []
    valid_empty = fetch_and_stage_genbank_publication_evidence(
        conn, "PZ955173.2", expected_source_sha256=hashlib.sha256(raw).hexdigest(), fetcher=lambda _: raw,
    )
    assert valid_empty == {
        "state": "no_citable_reference", "accession_version": "PZ955173.2",
        "source_content_sha256": hashlib.sha256(raw).hexdigest(), "staged": 0,
    }


@pytest.mark.parametrize("review_state", ["candidate_source_attested", "accepted_source_attested", "rejected"])
def test_duplicate_evidence_reports_existing_review_state_and_zero_new_rows(review_state):
    conn = Connection(
        [("6db28640-67fb-4808-90de-956a856366f7", "Schizophyllum commune", "species")],
        existing_review_state=review_state,
    )
    result = stage_genbank_publication_evidence(conn, genbank_xml())
    assert result["state"] == review_state
    assert result["staged"] == result["inserted"] == 0
    assert result["existing"] == 1
    assert result["evidence_receipts"] == [{
        "evidence_id": "b" * 32, "provider_source_record_id": "PZ955173.1#reference=1",
        "write_state": "existing", "review_state": review_state,
    }]
    assert conn.commits == 0
    assert not any("UPDATE bio.publication_taxon_evidence" in sql for sql, _ in conn.cursor_value.calls)


def test_additive_migration_keeps_candidates_separate_and_source_fields_immutable():
    migration = Path(__file__).parents[1] / "migrations" / "20261003_publication_taxon_evidence_OCT03_2026.sql"
    sql = migration.read_text(encoding="utf-8")

    assert sql.strip().startswith("-- Source-attested")
    assert "BEGIN;" in sql and sql.rstrip().endswith("COMMIT;")
    assert "CREATE TABLE IF NOT EXISTS bio.publication_taxon_evidence" in sql
    assert "UNIQUE (provider, provider_source_record_id, source_content_sha256, taxon_id)" in sql
    assert "CREATE TRIGGER trg_publication_taxon_evidence_immutable" in sql
    assert "source fields are immutable" in sql
    assert "a reviewer identity is required for evidence disposition" in sql
    assert "candidate_source_attested" in sql and "accepted_source_attested" in sql and "rejected" in sql
    assert "INSERT INTO bio.publication_taxon (" not in sql
