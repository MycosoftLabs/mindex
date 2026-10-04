from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from uuid import UUID

import pytest

from mindex_etl.research_identity_export import (
    AUTHORITY_SQL,
    MAX_LIMIT,
    MAX_TOTAL_SEQUENCE_UTF8_BYTES,
    ROWS_SQL,
    SCHEMA,
    SCHEMA_SQL,
    _REQUIRED_SCHEMA_TYPES,
    _canonical_json,
    _make_record,
    _resource_mapping,
    export_identity_snapshot,
)


TAXON_ID = UUID("6db28640-67fb-4808-90de-956a856366f7")
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
COMMIT = "a60f2f309c438f375313f8c843877dc9881aaf7b"
SEQUENCE = "acgt\nACGT "
SEQUENCE_HASH = hashlib.sha256(SEQUENCE.encode("utf-8")).hexdigest()


def sequence_row(**changes):
    row = {
        "id": 12,
        "accession": "PZ955173",
        "version": "PZ955173.1",
        "taxon_id": TAXON_ID,
        "taxon_exists": True,
        "gene": "ITS",
        "region": "ITS1",
        "sequence_type": "dna",
        "source": "genbank",
        "source_url": "https://www.ncbi.nlm.nih.gov/nuccore/PZ955173.1",
        "sequence_sha256": SEQUENCE_HASH,
        "sequence_utf8_bytes": len(SEQUENCE.encode("utf-8")),
        "metadata": {"taxon_linkage": {"state": "linked_unique_exact_external_id"}},
    }
    row.update(changes)
    return row


def test_exact_stored_genbank_its_identity_uses_real_row_and_taxon_types():
    record, diagnostics = _make_record(sequence_row())

    assert diagnostics == []
    assert record["resource"] == "its"
    assert record["sequence_row_id"] == 12 and type(record["sequence_row_id"]) is int
    assert record["canonical_taxon_id"] == str(TAXON_ID)
    assert record["provider"] == "genbank"
    assert record["accession"] == "PZ955173"
    assert record["version"] == "PZ955173.1"
    assert record["molecule"] == "dna"
    assert record["sequence_sha256"] == SEQUENCE_HASH
    assert record["sequence_hash_scope"] == "exact_stored_sequence_utf8_bytes"
    assert record["eligible_for_exact_identity_join"] is True


@pytest.mark.parametrize(
    ("row", "expected_resource", "expected_code"),
    [
        ({"source": "genbank", "sequence_type": "dna", "gene": "ITS", "region": "ITS1"}, "its", None),
        ({"source": "genbank", "sequence_type": "dna", "gene": None, "region": None}, None, "marker_missing"),
        ({"source": "genbank", "sequence_type": "dna", "gene": "LSU", "region": "LSU"}, None, "marker_unmapped_or_conflicting"),
        ({"source": "genbank", "sequence_type": "protein", "gene": "ITS", "region": None}, None, "provider_molecule_unmapped"),
        ({"source": "other", "sequence_type": "dna", "gene": "ITS", "region": None}, None, "provider_molecule_unmapped"),
        ({"source": "uniprot", "sequence_type": "protein", "gene": None, "region": None}, "uniprot", None),
        ({"source": "ensembl", "sequence_type": "dna", "gene": None, "region": None}, "ensembl", None),
    ],
)
def test_provider_resource_mapping_is_explicit(row, expected_resource, expected_code):
    resource, marker, diagnostic = _resource_mapping(row)

    assert resource == expected_resource
    assert diagnostic == expected_code
    if expected_resource == "its":
        assert marker == "its"


def test_missing_taxon_and_unknown_marker_remain_partial_not_positive():
    record, diagnostics = _make_record(sequence_row(taxon_id=None, gene=None, region=None))

    assert record["canonical_taxon_id"] is None
    assert record["taxon_association_state"] == "unlinked"
    assert record["eligible_for_exact_identity_join"] is False
    assert {d["code"] for d in diagnostics} == {"taxon_link_absent", "marker_missing"}


def test_declared_hash_conflict_is_retained_as_a_qualified_record():
    row = sequence_row(metadata={
        "sequence_sha256": "0" * 64,
        "taxon_linkage": {"state": "linked_unique_exact_external_id"},
    })
    record, diagnostics = _make_record(row)

    assert record["sequence_sha256"] == SEQUENCE_HASH
    assert record["sequence_hash_state"] == "conflict"
    assert record["eligible_for_exact_identity_join"] is False
    assert [d["code"] for d in diagnostics] == ["sequence_hash_conflict"]


def test_wrong_version_is_not_rewritten_or_admitted():
    record, diagnostics = _make_record(sequence_row(version="PZ955174.2"))

    assert record["version"] == "PZ955174.2"
    assert record["eligible_for_exact_identity_join"] is False
    assert "version_conflict" in {d["code"] for d in diagnostics}


def test_unversioned_accession_equality_does_not_claim_an_exact_version():
    row = sequence_row(
        accession="P00549", version="P00549", source="uniprot", sequence_type="protein",
        gene=None, region=None,
    )
    record, diagnostics = _make_record(row)

    assert record["accession"] == record["version"] == "P00549"
    assert record["resource"] == "uniprot"
    assert record["eligible_for_exact_identity_join"] is False
    assert "version_unversioned" in {d["code"] for d in diagnostics}


def test_fully_versioned_value_in_both_fields_is_exact():
    record, diagnostics = _make_record(sequence_row(accession="PZ955173.1", version="PZ955173.1"))

    assert diagnostics == []
    assert record["eligible_for_exact_identity_join"] is True


def test_total_export_byte_cap_omits_hash_and_qualifies_the_row():
    row = sequence_row(
        sequence_sha256=None,
        sequence_utf8_bytes=MAX_TOTAL_SEQUENCE_UTF8_BYTES + 1,
    )
    record, diagnostics = _make_record(row)

    assert record["sequence_sha256"] is None
    assert record["sequence_utf8_bytes"] == MAX_TOTAL_SEQUENCE_UTF8_BYTES + 1
    assert record["eligible_for_exact_identity_join"] is False
    assert "sequence_export_byte_limit" in {d["code"] for d in diagnostics}


def test_ambiguous_stored_linkage_is_preserved_as_an_ambiguous_conflict():
    row = sequence_row(metadata={"taxon_linkage": {"state": "ambiguous_exact_external_id"}})
    record, diagnostics = _make_record(row)

    assert record["canonical_taxon_id"] == str(TAXON_ID)
    assert record["taxon_linkage_provenance_state"] == "ambiguous_exact_external_id"
    assert record["eligible_for_exact_identity_join"] is False
    assert "taxon_link_ambiguous" in {item["code"] for item in diagnostics}


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.description = []
        self.closed = False

    def execute(self, statement, params=None):
        self.connection.calls.append((statement, params))
        if statement.startswith("BEGIN "):
            return
        if statement == AUTHORITY_SQL:
            if self.connection.authority_error:
                raise RuntimeError("private driver detail must not escape")
            self.rows = [{
                "database_name": "mindex_fixture",
                "system_identifier": "7692651516079110784",
                "captured_at": NOW,
                "transaction_read_only": "on",
                "transaction_isolation": "repeatable read",
            }]
        elif statement == SCHEMA_SQL:
            self.rows = [
                {"schema_name": schema, "table_name": table, "column_name": column,
                 "actual_udt_name": self.connection.schema_override.get((schema, table, column), kind)}
                for (schema, table, column), kind in _REQUIRED_SCHEMA_TYPES.items()
            ]
        elif statement == ROWS_SQL:
            self.rows = list(self.connection.rows)
            self.connection.row_params = params
        else:
            raise AssertionError("unexpected SQL")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows=(), *, authority_error=False, schema_override=None, idle=True):
        self.rows = list(rows)
        self.authority_error = authority_error
        self.schema_override = schema_override or {}
        self.idle = idle
        self.calls = []
        self.row_params = None
        self.rollbacks = 0
        self.cursor_value = FakeCursor(self)

    def cursor(self):
        return self.cursor_value

    def get_transaction_status(self):
        return 0 if self.idle else 2

    def rollback(self):
        self.rollbacks += 1


def assert_sealed(export):
    original = dict(export)
    expected = original.pop("export_sha256")
    assert hashlib.sha256(_canonical_json(original)).hexdigest() == expected


def test_producer_uses_fixed_parameterized_sql_and_readonly_receipt():
    connection = FakeConnection([sequence_row()])

    export = export_identity_snapshot(connection, producer_commit=COMMIT, accessions=["PZ955173"], limit=7)

    assert export["schema"] == SCHEMA
    assert export["status"] == "available"
    assert export["authority"]["instance"] == {
        "postgres_system_identifier": "7692651516079110784",
        "database": "mindex_fixture",
    }
    assert export["authority"]["transaction_read_only"] is True
    assert export["authority"]["transaction_isolation"] == "repeatable read"
    assert connection.row_params == (["PZ955173"], 8, MAX_TOTAL_SEQUENCE_UTF8_BYTES)
    assert [call[0] for call in connection.calls] == [
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY", AUTHORITY_SQL, SCHEMA_SQL, ROWS_SQL,
    ]
    assert connection.rollbacks == 1 and connection.cursor_value.closed
    assert_sealed(export)


def test_successful_no_match_is_empty_but_unlinked_or_unmapped_rows_are_partial():
    empty = export_identity_snapshot(FakeConnection(), producer_commit=COMMIT, accessions=["NO_MATCH"])
    partial = export_identity_snapshot(
        FakeConnection([sequence_row(taxon_id=None)]), producer_commit=COMMIT,
    )

    assert empty["status"] == "empty" and empty["records"] == []
    assert partial["status"] == "partial" and partial["records"][0]["canonical_taxon_id"] is None
    assert_sealed(empty)
    assert_sealed(partial)


def test_authority_failure_and_schema_drift_fail_unavailable_without_rows():
    authority = export_identity_snapshot(FakeConnection(authority_error=True), producer_commit=COMMIT)
    schema = export_identity_snapshot(
        FakeConnection([sequence_row()], schema_override={("bio", "genetic_sequence", "id"): "int8"}),
        producer_commit=COMMIT,
    )

    assert authority["status"] == "unavailable" and authority["records"] == []
    assert authority["diagnostics"] == [{"code": "authority_schema_or_query_unavailable"}]
    assert schema["status"] == "unavailable" and schema["records"] == []
    assert schema["diagnostics"][0]["code"] == "required_schema_incompatible"
    assert_sealed(authority)
    assert_sealed(schema)


def test_export_refuses_nonidle_connections_without_rolling_back_caller_work():
    connection = FakeConnection(idle=False)

    export = export_identity_snapshot(connection, producer_commit=COMMIT)

    assert export["status"] == "unavailable"
    assert export["diagnostics"] == [{"code": "fresh_idle_connection_required"}]
    assert connection.calls == []
    assert connection.rollbacks == 0


def test_empty_accession_filter_is_rejected_before_database_access():
    connection = FakeConnection()

    with pytest.raises(ValueError):
        export_identity_snapshot(connection, producer_commit=COMMIT, accessions=[])

    assert connection.calls == []


@pytest.mark.parametrize("values", [["PZ955173', NULL --"], ["PZ955173", 4]])
def test_invalid_accession_filter_is_rejected_without_interpreting_sql(values):
    connection = FakeConnection()

    with pytest.raises(ValueError):
        export_identity_snapshot(connection, producer_commit=COMMIT, accessions=values)

    assert connection.calls == []


@pytest.mark.parametrize("value", [0, MAX_LIMIT + 1, True])
def test_invalid_row_limit_is_rejected_before_database_access(value):
    connection = FakeConnection()
    with pytest.raises(ValueError):
        export_identity_snapshot(connection, producer_commit=COMMIT, limit=value)
    assert connection.calls == []
