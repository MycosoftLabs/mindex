from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException

from mindex_api.routers.genetics import get_sequence_by_accession


TAXON_ID = UUID("6db28640-67fb-4808-90de-956a856366f7")


class Result:
    def __init__(self, *, scalar=None, row=None):
        self.scalar = scalar
        self.row = row

    def scalar_one_or_none(self):
        return self.scalar

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class Session:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.rollback_calls = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def rollback(self):
        self.rollback_calls += 1


def sequence_row():
    return {
        "id": 32,
        "accession": "PZ955173",
        "version": "PZ955173.1",
        "taxon_id": TAXON_ID,
        "species_name": "Schizophyllum commune",
        "gene": "ITS",
        "region": "ITS",
        "sequence": "ACGT",
        "sequence_length": 4,
        "sequence_type": "dna",
        "source": "genbank",
        "source_url": "https://www.ncbi.nlm.nih.gov/nuccore/PZ955173.1",
        "definition": "captured accession fixture",
        "organism": "Schizophyllum commune",
        "pubmed_id": None,
        "doi": None,
        "metadata": {
            "source_taxon_ids": ["5334"],
            "taxon_linkage": {"state": "linked_unique_exact_external_id", "source": "ncbi"},
        },
    }


@pytest.mark.parametrize("requested", ["PZ955173.1", " PZ955173.1 "])
def test_versioned_get_matches_base_and_exact_stored_version(requested):
    session = Session(Result(scalar="bio.genetic_sequence"), Result(row=sequence_row()))

    response = asyncio.run(get_sequence_by_accession(requested, session))

    sql, params = session.calls[1]
    assert "WHERE accession = :accession" in sql
    assert "version = :requested_version" in sql
    assert params == {"accession": "PZ955173", "requested_version": "PZ955173.1"}
    assert response.accession == "PZ955173"
    assert response.accession_version == "PZ955173.1"
    assert response.taxon_id == TAXON_ID
    assert response.source_taxon_ids == ["5334"]
    assert response.taxon_linkage["state"] == "linked_unique_exact_external_id"
    assert session.rollback_calls == 0


def test_wrong_version_is_a_read_only_not_found():
    session = Session(Result(scalar="bio.genetic_sequence"), Result(row=None))

    with pytest.raises(HTTPException) as raised:
        asyncio.run(get_sequence_by_accession("PZ955173.2", session))

    assert raised.value.status_code == 404
    assert "PZ955173.2" in raised.value.detail
    assert session.calls[1][1] == {"accession": "PZ955173", "requested_version": "PZ955173.2"}
    assert session.rollback_calls == 0


def test_versionless_get_keeps_base_accession_lookup_semantics():
    session = Session(Result(scalar="bio.genetic_sequence"), Result(row=sequence_row()))

    response = asyncio.run(get_sequence_by_accession("PZ955173", session))

    sql, params = session.calls[1]
    assert "version = :requested_version" not in sql
    assert params == {"accession": "PZ955173"}
    assert response.accession == "PZ955173"
    assert response.accession_version == "PZ955173.1"


@pytest.mark.parametrize(
    ("results", "expected_code"),
    [
        ([RuntimeError("schema lookup failed")], "schema_check_unavailable"),
        ([Result(scalar="bio.genetic_sequence"), RuntimeError("stored read failed")], "genetics_query_unavailable"),
    ],
)
def test_stored_lookup_failures_remain_unavailable_and_do_not_fetch(results, expected_code):
    session = Session(*results)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(get_sequence_by_accession("PZ955173.1", session))

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == expected_code
    assert session.rollback_calls == 1
    assert len(session.calls) == len(results)
