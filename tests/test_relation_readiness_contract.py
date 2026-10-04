from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException

from mindex_api.routers.all_life import list_interactions, list_publications


class Result:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


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


def columns(*values):
    return Result(rows=[(value,) for value in values])


def test_interaction_read_preserves_either_direction_and_distinguishes_empty_from_unavailable():
    session = Session([
        columns("id", "source_taxon_id", "target_taxon_id", "interaction_type", "evidence_source", "evidence_url", "location", "metadata", "created_at"),
        Result(rows=[]),
        Result(scalar=0),
    ])
    taxon_id = UUID("6db28640-67fb-4808-90de-956a856366f7")

    response = asyncio.run(list_interactions(taxon_id=taxon_id, db=session, limit=20, offset=0))

    assert response["data"] == []
    assert response["data_state"] == "available"
    assert response["scope"] == "exact_taxon_either_direction"
    assert response["pagination"]["total"] == 0
    assert "source_taxon_id = :id OR target_taxon_id = :id" in session.calls[1][0]
    assert session.calls[1][1]["id"] == str(taxon_id)


def test_missing_interaction_schema_returns_503_instead_of_a_successful_empty_array():
    session = Session([columns("id", "source_taxon_id")])

    with pytest.raises(HTTPException) as raised:
        asyncio.run(list_interactions(
            taxon_id=UUID("6db28640-67fb-4808-90de-956a856366f7"), db=session, limit=20, offset=0,
        ))

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "schema_incomplete"
    assert len(session.calls) == 1


def test_publication_read_reports_linkage_provenance_gap_and_exact_pagination():
    session = Session([
        columns("publication_id", "taxon_id", "relevance_score", "created_at"),
        columns("id"),
        Result(rows=[]),
        Result(scalar=0),
    ])

    response = asyncio.run(list_publications(
        taxon_id=UUID("6db28640-67fb-4808-90de-956a856366f7"), db=session, limit=5, offset=10,
    ))

    assert response["data"] == []
    assert response["data_state"] == "available"
    assert response["association_provenance_state"] == "not_recorded_by_current_link_schema"
    assert response["pagination"] == {"limit": 5, "offset": 10, "total": 0}
    assert "pt.taxon_id = :id" in session.calls[2][0]
    assert session.calls[2][1] == {"id": "6db28640-67fb-4808-90de-956a856366f7", "lim": 5, "off": 10}


def test_publication_schema_check_database_error_is_typed_and_rolled_back():
    session = Session([RuntimeError("private connection string")])

    with pytest.raises(HTTPException) as raised:
        asyncio.run(list_publications(
            taxon_id=UUID("6db28640-67fb-4808-90de-956a856366f7"), db=session, limit=5, offset=0,
        ))

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "schema_check_unavailable"
    assert "private connection string" not in str(raised.value.detail)
    assert session.rollbacks == 1
