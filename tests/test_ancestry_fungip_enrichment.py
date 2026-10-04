"""Focused offline tests for optional ordinary-taxon enrichment and source search."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from mindex_api.contracts.v1.ancestry_index import FungiPIndexAvailability
from mindex_api.dependencies import PaginationParams
from mindex_api.routers import taxon as taxon_router
from mindex_api.services import ancestry_public_members as ancestry


@pytest.fixture(autouse=True)
def isolate_taxon_count_cache():
    """Keep fake SQL response ordering independent of previous list-route tests."""
    taxon_router._count_cache.clear()
    yield
    taxon_router._count_cache.clear()


CANONICAL_UUID = UUID("6eb9e962-05c3-4f2b-8ed1-598cbb2a0ac2")


class _Result:
    def __init__(self, *, one=None, rows=None, scalar=None, scalar_or_none=None):
        self._one = one
        self._rows = rows or []
        self._scalar = scalar
        self._scalar_or_none = scalar_or_none

    def mappings(self):
        return self

    def one(self):
        return self._one

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar_or_none


class _FakeSession:
    def __init__(self, results):
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


def _row(**overrides):
    data = {
        "species_id": "FG026",
        "stored_taxon_id": CANONICAL_UUID,
        "accepted_name": "Agaricus exampleus",
        "record": {
            "accepted_name": "Agaricus exampleus", "requested_name": "Agaricus requested",
            "common_name": "Example mushroom", "ticker": "FGX",
            "synonyms": ["Historical source name"], "catalog_review_flags": ["review_open"],
            "dna": {"accession_version": "OP1.1"},
        },
        "external_ids": [{"source": "gbif", "external_id": "42"}],
        "verified_its_sequence": None,
        "image_valid": True,
        "sequence_valid": True,
        "missing_data_flags": [],
        "validation_errors": [],
        "record_sha256": "a" * 64,
        "catalog_sha256": "b" * 64,
        "resolution_status": "resolved",
        "candidate_count": 1,
        "candidate_taxon_id": CANONICAL_UUID,
        "candidate_taxon_ids": [CANONICAL_UUID],
        "canonical_name": "Agaricus exampleus",
        "canonical_rank": "species",
        "canonical_kingdom": "Fungi",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_all_life_rows_survive_when_optional_fungip_table_is_unavailable():
    rows, status = await ancestry.load_public_fungip_members(
        _FakeSession([_Result(scalar_or_none=None)]), [CANONICAL_UUID],
    )
    assert rows == {}
    assert status == FungiPIndexAvailability(status="unavailable", reason="source_table_missing")


@pytest.mark.asyncio
async def test_ordinary_taxa_route_keeps_all_life_rows_when_index_is_missing():
    ordinary = {
        "id": CANONICAL_UUID, "canonical_name": "Agaricus exampleus", "rank": "species",
        "common_name": "Example mushroom", "author": None, "description": None,
        "source": "fixture", "metadata": {}, "kingdom": "Fungi", "lineage": [],
        "lineage_ids": [], "external_ids": {}, "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
        "obs_count": 2, "image_count": 0, "video_count": 0, "audio_count": 0,
        "genome_count": 0, "compound_link_count": 0, "interaction_count": 0,
        "publication_count": 0, "characteristic_count": 0,
    }
    session = _FakeSession([
        _Result(rows=[ordinary]), _Result(scalar=1), _Result(scalar_or_none=None),
    ])
    response = await taxon_router.list_taxa(
        pagination=PaginationParams(limit=20, offset=0), db=session,
        ids=None, q=None, rank=None, source=None, prefix=None, kingdom=None,
        lineage_contains=None, order_by="canonical_name", order="asc",
    )
    assert response.pagination.total == 1
    assert len(response.data) == 1
    assert response.data[0].id == CANONICAL_UUID
    assert response.data[0].fungip is None
    assert response.fungip_index.status == "unavailable"


@pytest.mark.asyncio
async def test_ordinary_taxa_route_attaches_only_linked_fungip_member():
    assert "LOWER(t.rank) = 'species'" in str(ancestry._PUBLIC_MEMBER_SQL)
    ordinary = {
        "id": CANONICAL_UUID, "canonical_name": "Agaricus exampleus", "rank": "species",
        "common_name": "Example mushroom", "author": None, "description": None,
        "source": "fixture", "metadata": {}, "kingdom": "Fungi", "lineage": [],
        "lineage_ids": [], "external_ids": {}, "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
        "obs_count": 2, "image_count": 0, "video_count": 0, "audio_count": 0,
        "genome_count": 0, "compound_link_count": 0, "interaction_count": 0,
        "publication_count": 0, "characteristic_count": 0,
    }
    member_row = {
        **_row(), "taxon_id": CANONICAL_UUID, "page_record_sha256": "a" * 64,
        "page_evidence": {key: True for key in (
            "name_checked", "taxonomy_checked", "dna_checked", "download_checked", "attribution_checked",
        )},
        "page_canonical_url": "https://example.invalid/ancestry/species/FG026",
        "page_taxon_id": CANONICAL_UUID, "canonical_common_name": "Example mushroom",
        "canonical_metadata": {}, "token_confirmed": False,
    }
    session = _FakeSession([
        _Result(rows=[ordinary]), _Result(scalar=1),
        _Result(scalar_or_none="fungip.species"), _Result(rows=[member_row]),
    ])
    response = await taxon_router.list_taxa(
        pagination=PaginationParams(limit=20, offset=0), db=session,
        ids=None, q=None, rank=None, source=None, prefix=None, kingdom=None,
        lineage_contains=None, order_by="canonical_name", order="asc",
    )
    assert response.fungip_index.status == "available"
    assert response.data[0].fungip.species_id == "FG026"
    assert response.data[0].fungip.mindex_uuid == CANONICAL_UUID
    assert response.data[0].fungip.identity_state == "linked"


@pytest.mark.asyncio
async def test_all_life_enrichment_errors_are_reported_without_fabricating_members():
    session = _FakeSession([_Result(scalar_or_none="fungip.species"), RuntimeError("query failed")])
    rows, status = await ancestry.load_public_fungip_members(session, [CANONICAL_UUID])
    assert rows == {}
    assert status == FungiPIndexAvailability(status="error", reason="query_failed")
    assert session.rollback_calls == 1


@pytest.mark.asyncio
async def test_unified_source_search_uses_stable_source_id_and_never_promotes_unresolved_candidate():
    unresolved = _row(
        stored_taxon_id=None, resolution_status="unresolved", candidate_count=1,
    )
    session = _FakeSession([
        _Result(one={"species_table": "fungip.species", "launch_table": None, "batch_table": None}),
        _Result(rows=[unresolved]),
    ])
    results, status = await ancestry.search_public_fungip(session, "Agaricus", 10)
    assert status.status == "available"
    assert results[0]["id"] == "fungip:FG026"
    assert results[0]["properties"]["identity_state"] == "unresolved"
    assert results[0]["properties"]["canonical_taxon_uuid"] is None
    assert results[0]["properties"]["requested_name"] == "Agaricus requested"
    assert results[0]["properties"]["synonyms"] == ["Historical source name"]
    assert results[0]["properties"]["catalog_review_flags"] == ["review_open"]
    assert session.calls[1][1]["pattern"] == "%Agaricus%"
    assert "record->>'requested_name' ILIKE :pattern" in session.calls[1][0]


@pytest.mark.asyncio
async def test_unified_source_search_links_only_exact_qualified_crosswalk():
    session = _FakeSession([
        _Result(one={"species_table": "fungip.species", "launch_table": None, "batch_table": None}),
        _Result(rows=[_row()]),
    ])
    results, status = await ancestry.search_public_fungip(session, "Agaricus", 10)
    assert status.status == "available"
    assert results[0]["properties"]["identity_state"] == "linked"
    assert results[0]["properties"]["canonical_taxon_uuid"] == str(CANONICAL_UUID)


@pytest.mark.asyncio
async def test_unified_search_marks_cross_kingdom_and_source_name_mismatch_ambiguous():
    cross_kingdom = _row(canonical_kingdom="Animalia")
    changed_parent_name = _row(accepted_name="Agaricus changed")
    session = _FakeSession([
        _Result(one={"species_table": "fungip.species", "launch_table": None, "batch_table": None}),
        _Result(rows=[cross_kingdom, changed_parent_name]),
    ])
    results, _ = await ancestry.search_public_fungip(session, "Agaricus", 10)
    assert [row["properties"]["identity_state"] for row in results] == ["ambiguous", "ambiguous"]


@pytest.mark.asyncio
async def test_unified_source_search_reports_missing_source_as_unavailable():
    results, status = await ancestry.search_public_fungip(
        _FakeSession([_Result(one={"species_table": None, "launch_table": None, "batch_table": None})]),
        "Agaricus", 10,
    )
    assert results == []
    assert status.status == "unavailable"
    assert status.reason == "source_table_missing"


@pytest.mark.asyncio
async def test_unified_source_search_respects_nonfungal_kingdom_filter_without_database_access():
    session = _FakeSession([])
    results, status = await ancestry.search_public_fungip(session, "Agaricus", 10, kingdom="Plantae")
    assert results == []
    assert status.status == "available"
    assert session.calls == []


@pytest.mark.asyncio
async def test_source_search_mint_match_uses_only_currently_validated_associations(monkeypatch):
    async def validated(_session):
        return ({"FG026": {
            "mint_address": "mint-search-me", "launch_tx": None,
            "synonyms": ["Historical source name", "Launch synonym"],
            "catalog_review_flags": ["review_open", "launch_review"],
        }}, {"FG027"})

    monkeypatch.setattr(ancestry, "load_validated_first40_associations", validated)
    session = _FakeSession([
        _Result(one={
            "species_table": "fungip.species",
            "launch_table": "fungip.first40_launch_association",
            "batch_table": "fungip.first40_source_batch",
        }),
        _Result(rows=[_row()]),
    ])
    results, status = await ancestry.search_public_fungip(session, "mint-search-me", 10)
    assert status.status == "available"
    assert results[0]["properties"]["synonyms"] == ["Historical source name", "Launch synonym"]
    assert results[0]["properties"]["catalog_review_flags"] == ["review_open", "launch_review"]
    assert session.calls[1][1]["launch_ids"] == ["FG026"]


@pytest.mark.asyncio
async def test_first40_loader_marks_parent_binding_mismatch_invalid(monkeypatch):
    def validate(row, parent):
        if row["ticker"] != parent["record"]["ticker"]:
            raise ValueError("parent record changed")
        return {"species_id": row["species_id"], "mint_address": row["mint_address"]}

    monkeypatch.setattr(ancestry, "public_first40_launch", validate)
    session = _FakeSession([_Result(rows=[
        {
            "species_id": "FG026", "ticker": "FGX", "mint_address": "mint-good",
            "parent_accepted_name": "Agaricus exampleus",
            "parent_record": {"ticker": "FGX"}, "parent_image_valid": True,
            "parent_sequence_valid": True,
        },
        {
            "species_id": "FG027", "ticker": "OLD", "mint_address": "mint-stale",
            "parent_accepted_name": "Agaricus other",
            "parent_record": {"ticker": "NEW"}, "parent_image_valid": True,
            "parent_sequence_valid": True,
        },
    ])])
    valid, invalid = await ancestry.load_validated_first40_associations(session)
    assert set(valid) == {"FG026"}
    assert invalid == {"FG027"}
