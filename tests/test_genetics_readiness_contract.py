from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from mindex_api.routers.genetics import _resolve_ncbi_taxon_link, list_genetic_sequences


class Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def fetchall(self):
        return self._rows


class Session:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def rollback(self):
        self.rollbacks += 1


def test_genetics_exact_taxon_empty_has_complete_pagination_and_exact_selector():
    taxon_id = UUID("6db28640-67fb-4808-90de-956a856366f7")
    session = Session([Result(scalar=True), Result(scalar=0), Result(rows=[])])

    response = asyncio.run(list_genetic_sequences(
        pagination=SimpleNamespace(limit=25, offset=10),
        db=session,
        search=None,
        gene=None,
        source=None,
        species=None,
        taxon_id=taxon_id,
        kingdom=None,
        min_length=None,
        max_length=None,
    ))

    assert response.data == []
    assert response.pagination == {"limit": 25, "offset": 10, "total": 0}
    assert "gs.taxon_id = :taxon_id" in session.calls[1][0]
    assert session.calls[1][1]["taxon_id"] == str(taxon_id)


def test_genetics_projection_exposes_versioned_accession_and_linkage_provenance():
    # This is a synthetic row identity for a fixture record, not the splitgill UUID.
    taxon_id = UUID("c8814bf5-6317-4792-a8b2-5982404caa01")
    session = Session([
        Result(scalar=True),
        Result(scalar=1),
        Result(rows=[{
            "id": 9,
            "accession": "LT627806",
            "version": "LT627806.1",
            "taxon_id": taxon_id,
            "species_name": "Pleurotus ostreatus",
            "gene": "ITS",
            "region": "ITS1",
            "sequence": "ACGTT",
            "sequence_length": 5,
            "sequence_type": "dna",
            "source": "genbank",
            "source_url": "https://www.ncbi.nlm.nih.gov/nuccore/LT627806.1",
            "definition": "fixture record",
            "organism": "Pleurotus ostreatus",
            "pubmed_id": None,
            "doi": None,
            "metadata": {
                "source_taxon_ids": ["5322"],
                "taxon_linkage": {"state": "linked_unique_exact_external_id", "source": "ncbi"},
            },
        }]),
    ])

    response = asyncio.run(list_genetic_sequences(
        pagination=SimpleNamespace(limit=25, offset=0), db=session,
        search=None, gene=None, source=None, species=None, taxon_id=taxon_id,
        kingdom=None, min_length=None, max_length=None,
    ))

    sequence = response.data[0]
    assert sequence.accession_version == "LT627806.1"
    assert sequence.taxon_id == taxon_id
    assert sequence.source_taxon_ids == ["5322"]
    assert sequence.taxon_linkage["state"] == "linked_unique_exact_external_id"


def test_genetics_schema_check_database_error_is_503_not_empty():
    session = Session([RuntimeError("private db detail")])

    with pytest.raises(HTTPException) as raised:
        asyncio.run(list_genetic_sequences(
            pagination=SimpleNamespace(limit=25, offset=0),
            db=session,
            search=None,
            gene=None,
            source=None,
            species=None,
            taxon_id=None,
            kingdom=None,
            min_length=None,
            max_length=None,
        ))

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "schema_check_unavailable"
    assert "private db detail" not in str(raised.value.detail)
    assert session.rollbacks == 1


def test_api_genbank_crosswalk_requires_exact_numeric_id_and_source_name_agreement():
    expected = UUID("c8814bf5-6317-4792-a8b2-5982404caa01")
    session = Session([Result(rows=[(expected, "Pleurotus ostreatus", "species")])])

    taxon_id, state = asyncio.run(_resolve_ncbi_taxon_link(session, "5322", "Pleurotus ostreatus"))

    assert taxon_id == expected
    assert state == {
        "state": "linked_unique_exact_external_id",
        "source": "ncbi",
        "source_ids": ["5322"],
        "source_name": "Pleurotus ostreatus",
        "canonical_name": "Pleurotus ostreatus",
        "canonical_rank": "species",
    }
    assert "x.source = 'ncbi' AND x.external_id = :external_id" in session.calls[0][0]
    assert "JOIN core.taxon AS t ON t.id = x.taxon_id" in session.calls[0][0]
    assert session.calls[0][1] == {"external_id": "5322"}


def test_api_wrong_taxid_cannot_attach_splitgill_on_mismatched_canonical_name():
    splitgill_id = UUID("6db28640-67fb-4808-90de-956a856366f7")
    session = Session([Result(rows=[(splitgill_id, "Schizophyllum commune", "species")])])

    taxon_id, state = asyncio.run(_resolve_ncbi_taxon_link(session, "5322", "Pleurotus ostreatus"))

    assert taxon_id is None
    assert state == {
        "state": "source_name_mismatch", "source": "ncbi", "source_ids": ["5322"],
        "source_name": "Pleurotus ostreatus", "candidate_taxon_id": str(splitgill_id),
        "candidate_name": "Schizophyllum commune", "candidate_rank": "species",
    }


def test_api_exact_splitgill_taxid_and_name_link():
    splitgill_id = UUID("6db28640-67fb-4808-90de-956a856366f7")
    session = Session([Result(rows=[(splitgill_id, "Schizophyllum commune", "species")])])

    taxon_id, state = asyncio.run(_resolve_ncbi_taxon_link(session, "5334", "Schizophyllum commune"))

    assert taxon_id == splitgill_id
    assert state["state"] == "linked_unique_exact_external_id"
    assert state["source_ids"] == ["5334"]


def test_api_genbank_crosswalk_keeps_missing_and_ambiguous_states_distinct():
    missing = Session([Result(rows=[])])
    assert asyncio.run(_resolve_ncbi_taxon_link(missing, ["5322"])) == (
        None,
        {"state": "unlinked_exact_external_id", "source": "ncbi", "source_ids": ["5322"]},
    )
    ambiguous = Session([Result(rows=[
        (UUID("c8814bf5-6317-4792-a8b2-5982404caa01"), "Pleurotus ostreatus", "species"),
        (UUID("71bc4967-f421-45ab-86eb-d417e1293d90"), "Pleurotus ostreatus", "species"),
    ])])
    taxon_id, state = asyncio.run(_resolve_ncbi_taxon_link(ambiguous, ["5322"]))
    assert taxon_id is None
    assert state["state"] == "ambiguous_exact_external_id"
