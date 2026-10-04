from __future__ import annotations

import json
from contextlib import contextmanager
from uuid import UUID

from mindex_etl.jobs.sync_genbank_genomes import _genbank_metadata, _resolve_taxon_link
from mindex_etl.jobs import sync_genbank_genomes as sync_job
from mindex_etl.sources.genbank import _parse_genbank_xml, map_genbank_to_genome


def gbseq_xml(taxon_ids: list[str]) -> str:
    qualifiers = "".join(
        f"<GBQualifier><GBQualifier_name>db_xref</GBQualifier_name><GBQualifier_value>taxon:{value}</GBQualifier_value></GBQualifier>"
        for value in taxon_ids
    )
    return f"""<GBSet><GBSeq>
      <GBSeq_primary-accession>LT627806</GBSeq_primary-accession>
      <GBSeq_accession-version>LT627806.1</GBSeq_accession-version>
      <GBSeq_length>5</GBSeq_length><GBSeq_moltype>DNA</GBSeq_moltype>
      <GBSeq_definition>Fungal rRNA example</GBSeq_definition>
      <GBSeq_organism>Pleurotus ostreatus</GBSeq_organism>
      <GBSeq_taxonomy>Fungi; Basidiomycota; Pleurotus</GBSeq_taxonomy>
      <GBSeq_sequence>acgtt</GBSeq_sequence>
      <GBSeq_feature-table><GBFeature><GBFeature_key>source</GBFeature_key>
        <GBFeature_quals>{qualifiers}</GBFeature_quals>
      </GBFeature></GBSeq_feature-table>
    </GBSeq></GBSet>"""


class CrosswalkCursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


def test_genbank_parser_retains_accession_version_and_one_exact_source_taxon_id():
    parsed = map_genbank_to_genome(_parse_genbank_xml(gbseq_xml(["5322", "5322"]))[0])

    assert parsed["accession"] == "LT627806"
    assert parsed["accession_version"] == "LT627806.1"
    assert parsed["source_url"] == "https://www.ncbi.nlm.nih.gov/nuccore/LT627806.1"
    assert parsed["source_taxon_ids"] == ["5322"]
    assert parsed["taxon_id"] == "5322"


def test_genbank_parser_withholds_link_for_multiple_source_taxon_ids():
    parsed = map_genbank_to_genome(_parse_genbank_xml(gbseq_xml(["5322", "1137138"]))[0])

    assert parsed["source_taxon_ids"] == ["5322", "1137138"]
    assert parsed["taxon_id"] is None


def test_taxon_link_uses_exact_ncbi_id_and_requires_matching_source_name():
    # The matching UUID is synthetic; this does not assert a live crosswalk.
    expected = "c8814bf5-6317-4792-a8b2-5982404caa01"
    cursor = CrosswalkCursor([(expected, "Pleurotus ostreatus", "species")])

    taxon_id, state = _resolve_taxon_link(cursor, {
        "organism": "Pleurotus ostreatus",
        "source_taxon_ids": ["5322", "5322"],
    })

    assert taxon_id == expected
    assert state == {
        "state": "linked_unique_exact_external_id",
        "source": "ncbi",
        "source_ids": ["5322"],
        "source_name": "Pleurotus ostreatus",
        "canonical_name": "Pleurotus ostreatus",
        "canonical_rank": "species",
    }
    sql, params = cursor.calls[0]
    assert "x.source = %s AND x.external_id = %s" in sql
    assert "JOIN core.taxon AS t ON t.id = x.taxon_id" in sql
    assert "ILIKE" not in sql
    assert params == ("ncbi", "5322")


def test_taxon_link_accepts_psycopg_dict_rows_and_a_scalar_source_id():
    expected = "c8814bf5-6317-4792-a8b2-5982404caa01"
    cursor = CrosswalkCursor([{
        "taxon_id": UUID(expected), "canonical_name": "Pleurotus ostreatus", "rank": "species",
    }])

    taxon_id, state = _resolve_taxon_link(cursor, {
        "source_taxon_ids": "5322", "organism": "Pleurotus ostreatus",
    })

    assert taxon_id == expected
    assert state["state"] == "linked_unique_exact_external_id"
    assert cursor.calls[0][1] == ("ncbi", "5322")


def test_wrong_ncbi_taxid_cannot_attach_splitgill_even_if_crosswalk_is_corrupt():
    splitgill_uuid = "6db28640-67fb-4808-90de-956a856366f7"
    cursor = CrosswalkCursor([(splitgill_uuid, "Schizophyllum commune", "species")])

    taxon_id, state = _resolve_taxon_link(cursor, {
        "source_taxon_ids": ["5322"], "organism": "Pleurotus ostreatus",
    })

    assert taxon_id is None
    assert state == {
        "state": "source_name_mismatch", "source": "ncbi", "source_ids": ["5322"],
        "source_name": "Pleurotus ostreatus", "candidate_taxon_id": splitgill_uuid,
        "candidate_name": "Schizophyllum commune", "candidate_rank": "species",
    }


def test_splitgill_ncbi_taxid_with_exact_name_can_link():
    splitgill_uuid = "6db28640-67fb-4808-90de-956a856366f7"
    cursor = CrosswalkCursor([(splitgill_uuid, "Schizophyllum commune", "species")])

    taxon_id, state = _resolve_taxon_link(cursor, {
        "source_taxon_ids": ["5334"], "organism": "Schizophyllum commune",
    })

    assert taxon_id == splitgill_uuid
    assert state["state"] == "linked_unique_exact_external_id"
    assert state["source_ids"] == ["5334"]


def test_unique_crosswalk_without_source_name_is_withheld():
    cursor = CrosswalkCursor([("c8814bf5-6317-4792-a8b2-5982404caa01", "Pleurotus ostreatus", "species")])

    taxon_id, state = _resolve_taxon_link(cursor, {"source_taxon_ids": ["5322"]})

    assert taxon_id is None
    assert state["state"] == "source_name_missing_unverified"


def test_taxon_link_keeps_empty_ambiguous_and_missing_states_distinct():
    unresolved = CrosswalkCursor([])
    assert _resolve_taxon_link(unresolved, {"source_taxon_ids": ["5322"]}) == (
        None,
        {"state": "unlinked_exact_external_id", "source": "ncbi", "source_ids": ["5322"]},
    )
    assert _resolve_taxon_link(CrosswalkCursor([]), {"source_taxon_ids": []}) == (
        None,
        {"state": "source_taxon_id_missing", "source": "ncbi", "source_ids": []},
    )
    ambiguous_ids = CrosswalkCursor([])
    assert _resolve_taxon_link(ambiguous_ids, {"source_taxon_ids": ["5322", "1137138"]})[1]["state"] == (
        "source_taxon_id_ambiguous_or_invalid"
    )
    assert ambiguous_ids.calls == []

    multiple_matches = CrosswalkCursor([("canonical-a",), ("canonical-b",)])
    assert _resolve_taxon_link(multiple_matches, {"source_taxon_ids": ["5322"]})[1]["state"] == (
        "ambiguous_exact_external_id"
    )


def test_linkage_metadata_preserves_source_taxonomy_and_version():
    value = _genbank_metadata(
        {"accession": "LT627806", "accession_version": "LT627806.1", "metadata": {"taxonomy": "Fungi"}},
        {"state": "unlinked_exact_external_id", "source": "ncbi", "source_ids": ["5322"]},
    )

    assert json.loads(value) == {
        "accession_version": "LT627806.1",
        "source_taxon_ids": ["5322"],
        "taxon_linkage": {
            "state": "unlinked_exact_external_id",
            "source": "ncbi",
            "source_ids": ["5322"],
        },
        "taxonomy": "Fungi",
    }


class ImportCursor:
    def __init__(self, conn):
        self.conn = conn
        self.calls = []
        self.rowcount = 1
        self.last_query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        self.last_query = sql
        self.calls.append((sql, params))
        if "FROM bio.genetic_sequence WHERE accession" in sql:
            return
        if "FROM core.taxon_external_id" in sql:
            self.conn.crosswalk_params.append(params)

    def fetchone(self):
        return self.conn.existing_row

    def fetchall(self):
        return self.conn.crosswalk_rows


class ImportConnection:
    def __init__(self, *, existing_row=None, crosswalk_rows=None):
        self.existing_row = existing_row
        self.crosswalk_rows = crosswalk_rows or []
        self.crosswalk_params = []
        self.cursors = []

    def cursor(self):
        cursor = ImportCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        pass


def test_genbank_importer_inserts_only_resolved_uuid_and_audits_source_identity(monkeypatch):
    # The cursor supplies an explicit synthetic exact crosswalk. This tests the
    # importer contract; it does not claim NCBI 5322 is the splitgill taxon.
    resolved_fixture_uuid = "c8814bf5-6317-4792-a8b2-5982404caa01"
    conn = ImportConnection(crosswalk_rows=[(UUID(resolved_fixture_uuid), "Pleurotus ostreatus", "species")])

    @contextmanager
    def fake_db_session():
        yield conn

    monkeypatch.setattr(sync_job, "db_session", fake_db_session)
    monkeypatch.setattr(sync_job.genbank, "iter_fungal_genomes", lambda **_: iter([{
        "accession": "LT627806",
        "accession_version": "LT627806.1",
        "organism": "Pleurotus ostreatus",
        "source_taxon_ids": ["5322"],
        "taxon_id": "5322",
        "sequence": "ACGTT",
        "sequence_length": 5,
        "molecule_type": "DNA",
        "definition": "fixture-backed importer input",
        "source_url": "https://www.ncbi.nlm.nih.gov/nuccore/LT627806.1",
        "metadata": {"taxonomy": "Fungi; Basidiomycota; Pleurotus"},
    }]))

    assert sync_job.sync_genbank_genomes(max_pages=1) == 1

    assert conn.crosswalk_params == [("ncbi", "5322")]
    insert_sql, params = conn.cursors[0].calls[-1]
    assert "INSERT INTO bio.genetic_sequence" in insert_sql
    assert "taxon_id" in insert_sql and "%s::jsonb" in insert_sql
    assert params[1] == resolved_fixture_uuid
    assert params[11] == "LT627806.1"
    metadata = json.loads(params[-1])
    assert metadata["source_taxon_ids"] == ["5322"]
    assert metadata["taxon_linkage"]["state"] == "linked_unique_exact_external_id"


def test_its_importer_uses_the_same_exact_taxon_crosswalk_and_updates_existing_accessions(monkeypatch):
    # As above, this synthetic crosswalk proves exact lookup behavior only.
    resolved_fixture_uuid = "c8814bf5-6317-4792-a8b2-5982404caa01"
    conn = ImportConnection(existing_row=(1,), crosswalk_rows=[(
        UUID(resolved_fixture_uuid), "Pleurotus ostreatus", "species",
    )])

    @contextmanager
    def fake_db_session():
        yield conn

    monkeypatch.setattr(sync_job, "db_session", fake_db_session)
    monkeypatch.setattr(sync_job.genbank, "iter_fungal_sequences", lambda **_: iter([{
        "accession": "LT627806",
        "accession_version": "LT627806.1",
        "organism": "Pleurotus ostreatus",
        "source_taxon_ids": ["5322"],
        "taxon_id": "5322",
        "sequence": "ACGTT",
        "sequence_length": 5,
        "molecule_type": "DNA",
        "region": "ITS1",
        "definition": "fixture-backed ITS input",
        "source_url": "https://www.ncbi.nlm.nih.gov/nuccore/LT627806.1",
        "metadata": {"taxonomy": "Fungi; Basidiomycota; Pleurotus"},
    }]))

    assert sync_job.sync_genbank_its_sequences(max_pages=1) == 1

    assert conn.crosswalk_params == [("ncbi", "5322")]
    insert_sql, params = conn.cursors[0].calls[-1]
    assert "INSERT INTO bio.genetic_sequence" in insert_sql
    assert "ON CONFLICT (accession) DO UPDATE" in insert_sql
    assert params[1] == resolved_fixture_uuid
    assert params[11] == "LT627806.1"


def test_its_importer_marks_preserved_legacy_link_unverified_when_exact_crosswalk_is_missing(monkeypatch):
    existing_taxon = UUID("71bc4967-f421-45ab-86eb-d417e1293d90")
    conn = ImportConnection(existing_row=(1, existing_taxon), crosswalk_rows=[])

    @contextmanager
    def fake_db_session():
        yield conn

    monkeypatch.setattr(sync_job, "db_session", fake_db_session)
    monkeypatch.setattr(sync_job.genbank, "iter_fungal_sequences", lambda **_: iter([{
        "accession": "LT627806",
        "accession_version": "LT627806.1",
        "organism": "Pleurotus ostreatus",
        "source_taxon_ids": ["5322"],
        "sequence": "ACGTT",
        "sequence_length": 5,
        "molecule_type": "DNA",
        "region": "ITS1",
        "definition": "fixture-backed ITS input",
        "metadata": {"taxonomy": "Fungi; Basidiomycota; Pleurotus"},
    }]))

    assert sync_job.sync_genbank_its_sequences(max_pages=1) == 1

    insert_sql, params = conn.cursors[0].calls[-1]
    assert "taxon_id = COALESCE(EXCLUDED.taxon_id, bio.genetic_sequence.taxon_id)" in insert_sql
    assert params[1] is None
    metadata = json.loads(params[-1])
    assert metadata["taxon_linkage"] == {
        "state": "existing_link_preserved_unverified",
        "preserved_taxon_id": str(existing_taxon),
        "source": "ncbi",
        "source_ids": ["5322"],
    }
