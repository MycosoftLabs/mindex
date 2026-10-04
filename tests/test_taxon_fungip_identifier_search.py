from __future__ import annotations

from uuid import UUID

import pytest

from mindex_api.contracts.v1.ancestry_index import FungiPIndexAvailability
from mindex_api.dependencies import PaginationParams
from mindex_api.routers import taxon as taxon_router
from mindex_api.services import ancestry_public_members as ancestry


FG032_TAXON_ID = UUID("6db28640-67fb-4808-90de-956a856366f7")


class Result:
    def __init__(self, *, value=None, rows=None):
        self.value = value
        self.rows = rows or []

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.statements = []
        self.rollback_calls = 0

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def rollback(self):
        self.rollback_calls += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("term", ["FG032", "SPLIT", "PZ955173.1"])
async def test_identifier_search_resolves_only_a_current_exact_fungip_link(term):
    session = Session([
        Result(value="fungip.species"),
        Result(rows=[{"id": FG032_TAXON_ID}]),
    ])

    ids, availability = await ancestry.search_validated_fungip_taxon_ids(session, f"%{term}%")

    assert ids == [FG032_TAXON_ID]
    assert availability.status == "available"
    sql = session.statements[1][0]
    assert "source.species_id ILIKE :query_pattern" in sql
    assert "source.record->>'ticker' ILIKE :query_pattern" in sql
    assert "source.record->'dna'->>'accession_version' ILIKE :query_pattern" in sql
    assert "external_id.source = source_identifier.value->>'source'" in sql
    assert "matches.candidate_count = 1" in sql
    assert "source.taxon_id = matches.candidate_taxon_id" in sql
    assert "taxon.canonical_name = source.record->>'accepted_name'" in sql


@pytest.mark.asyncio
async def test_identifier_search_keeps_optional_index_unavailable_and_query_errors_nonfatal():
    absent, status = await ancestry.search_validated_fungip_taxon_ids(
        Session([Result(value=None)]), "%FG032%",
    )
    assert absent == []
    assert status.status == "unavailable"
    assert status.reason == "source_table_missing"

    failed_session = Session([Result(value="fungip.species"), RuntimeError("private fixture error")])
    absent, status = await ancestry.search_validated_fungip_taxon_ids(failed_session, "%FG032%")
    assert absent == []
    assert status.status == "error"
    assert status.reason == "query_failed"
    assert failed_session.rollback_calls == 1


@pytest.mark.asyncio
async def test_linked_identifier_is_in_where_clause_before_count_and_page(monkeypatch):
    captured = {}

    async def search_ids(_db, query_pattern):
        captured["query_pattern"] = query_pattern
        return [FG032_TAXON_ID], FungiPIndexAvailability(status="available")

    async def list_core_page(_db, *, where_sql, params, by_popularity, order_normalized):
        captured.update({
            "where_sql": where_sql,
            "params": dict(params),
            "by_popularity": by_popularity,
            "order_normalized": order_normalized,
        })
        return [], 1

    async def load_members(_db, ids):
        assert ids == []
        return {}, FungiPIndexAvailability(status="unavailable", reason="source_table_missing")

    monkeypatch.setattr(taxon_router, "search_validated_fungip_taxon_ids", search_ids)
    monkeypatch.setattr(taxon_router, "_list_taxa_core_page", list_core_page)
    monkeypatch.setattr(taxon_router, "load_public_fungip_members", load_members)

    response = await taxon_router.list_taxa(
        pagination=PaginationParams(limit=500, offset=500),
        db=object(),
        ids=None,
        q="PZ955173.1",
        rank=None,
        source=None,
        prefix=None,
        kingdom=None,
        lineage_contains=None,
        order_by="canonical_name",
        order="asc",
    )

    assert response.pagination.total == 1
    assert captured["query_pattern"] == "%PZ955173.1%"
    assert "id = ANY(CAST(:fungip_taxon_ids AS uuid[]))" in captured["where_sql"]
    assert captured["params"]["fungip_taxon_ids"] == [FG032_TAXON_ID]
    assert captured["params"]["limit"] == 500
    assert captured["params"]["offset"] == 500
    assert response.fungip_index.status == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_status", "expected_status"),
    [
        (FungiPIndexAvailability(status="error", reason="query_failed"), "error"),
        (FungiPIndexAvailability(status="unavailable", reason="source_table_missing"), "unavailable"),
    ],
)
async def test_identifier_lookup_status_survives_empty_page_enrichment(
    monkeypatch, search_status, expected_status,
):
    page_called = False
    ordinary_taxon_id = UUID("f70e8f95-bf95-4da6-9df5-93d4475b4b78")
    ordinary_name_rows = (
        [{
            "id": ordinary_taxon_id,
            "canonical_name": "Agaricus ordinaryus",
            "rank": "species",
            "created_at": "2026-10-03T00:00:00Z",
            "updated_at": None,
        }]
        if search_status.status == "error" else []
    )

    async def failed_lookup(_db, _query_pattern):
        return [], search_status

    async def empty_core_page(_db, **_kwargs):
        nonlocal page_called
        page_called = True
        return ordinary_name_rows, len(ordinary_name_rows)

    async def successful_empty_enrichment(_db, ids):
        assert ids == ([ordinary_taxon_id] if ordinary_name_rows else [])
        return {}, FungiPIndexAvailability(status="available")

    monkeypatch.setattr(taxon_router, "search_validated_fungip_taxon_ids", failed_lookup)
    monkeypatch.setattr(taxon_router, "_list_taxa_core_page", empty_core_page)
    monkeypatch.setattr(taxon_router, "load_public_fungip_members", successful_empty_enrichment)

    response = await taxon_router.list_taxa(
        pagination=PaginationParams(limit=120, offset=0),
        db=object(),
        ids=None,
        q="FG032",
        rank=None,
        source=None,
        prefix=None,
        kingdom=None,
        lineage_contains=None,
        order_by="canonical_name",
        order="asc",
    )

    assert page_called
    if search_status.status == "error":
        assert len(response.data) == 1
        assert response.data[0].canonical_name == "Agaricus ordinaryus"
        assert response.pagination.total == 1
    else:
        assert response.data == []
        assert response.pagination.total == 0
    assert response.fungip_index.status == expected_status
    assert response.fungip_index.reason == search_status.reason
