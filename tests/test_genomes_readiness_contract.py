from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException

from mindex_api.routers.genomes import get_genomes


class Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def rollback(self):
        self.rollbacks += 1


def call(session, *, taxon_id=None):
    return asyncio.run(
        get_genomes(
            session=session,
            species=None,
            taxon_id=taxon_id,
            kingdom=None,
            limit=100,
            offset=0,
        )
    )


def test_missing_genome_table_is_not_reported_as_a_successful_empty_result():
    with pytest.raises(HTTPException) as raised:
        call(Session([Result(scalar=False)]))

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "schema_unavailable"


def test_readiness_database_error_is_unavailable_and_rolled_back():
    session = Session([RuntimeError("private connection detail")])

    with pytest.raises(HTTPException) as raised:
        call(session)

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "schema_check_unavailable"
    assert "private connection detail" not in str(raised.value.detail)
    assert session.rollbacks == 1


def test_successful_empty_exact_taxon_query_has_available_schema_metadata():
    session = Session([Result(scalar=True), Result(scalar=0), Result(rows=[])])
    taxon_id = UUID("6db28640-67fb-4808-90de-956a856366f7")

    result = call(session, taxon_id=taxon_id)

    assert result["genomes"] == []
    assert result["pagination"]["total"] == 0
    assert result["data_state"] == "available"
    assert result["schema_state"] == "ready"
    assert result["scope"] == "exact_taxon"
    assert "g.taxon_id = :taxon_id" in session.calls[1][0]
    assert session.calls[1][1]["taxon_id"] == str(taxon_id)


def test_genome_count_query_failure_is_not_reported_as_empty():
    session = Session([Result(scalar=True), RuntimeError("column missing")])

    with pytest.raises(HTTPException) as raised:
        call(session)

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "schema_or_query_unavailable"
    assert session.rollbacks == 1
