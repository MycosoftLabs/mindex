"""Offline all-species list/stats behavior; fake SQL results, no DB."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from mindex_api.contracts.v1.ancestry_index import FungiPIndexAvailability
from mindex_api.dependencies import PaginationParams
from mindex_api.routers import taxon as route


TAXON_ID = UUID("0c1f3a52-5d0e-4a51-9a7e-6f1f8d1c2b11")


class Result:
    def __init__(self, *, rows=None, scalar=None):
        self.rows, self.scalar = rows or [], scalar

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar


class Session:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def rollback(self):
        self.rollbacks += 1


def row():
    return {
        "id": TAXON_ID, "canonical_name": "Abies alba", "rank": "species", "common_name": "Silver fir",
        "author": "Mill.", "description": None, "source": "gbif", "metadata": {}, "kingdom": "Plantae",
        "lineage": ["Plantae", "Pinaceae"], "lineage_ids": [], "external_ids": {"gbif": "2685484"},
        "created_at": datetime(2026, 10, 3, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 3, tzinfo=timezone.utc),
        "obs_count": 0, "image_count": 0, "video_count": 0, "audio_count": 0, "genome_count": 0,
        "compound_link_count": 0, "interaction_count": 0, "publication_count": 0, "characteristic_count": 0,
    }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    route._count_cache.clear()

    async def members(db, taxon_ids):
        return {}, FungiPIndexAvailability(status="available")

    async def search_ids(db, query_pattern):
        return [], FungiPIndexAvailability(status="unavailable", reason="source_table_missing")

    monkeypatch.setattr(route, "load_public_fungip_members", members)
    monkeypatch.setattr(route, "search_validated_fungip_taxon_ids", search_ids)


async def call_list(db, **kwargs):
    defaults = dict(
        ids=None, q=None, rank=None, source=None, prefix=None, kingdom=None,
        lineage_contains=None, order_by="canonical_name", order="asc",
    )
    defaults.update(kwargs)
    pagination = PaginationParams(limit=kwargs.pop("limit", 50), offset=kwargs.pop("offset", 0))
    defaults.pop("limit", None)
    defaults.pop("offset", None)
    return await route.list_taxa(pagination=pagination, db=db, **defaults)


@pytest.mark.asyncio
async def test_species_rank_includes_abbreviated_sources_and_uses_core_page_path():
    db = Session(Result(rows=[row()]), Result(scalar=3_500_000))
    response = await call_list(db, rank="species")
    page_sql, page_params = db.calls[0]
    assert "FROM core.taxon" in page_sql and "LIMIT :limit OFFSET :offset" in page_sql
    assert page_params["rank_variants"] == ["species", "sp."]
    assert response.pagination.total == 3_500_000
    assert response.data[0].canonical_name == "Abies alba"


@pytest.mark.asyncio
async def test_total_is_cached_across_pages_of_the_same_filter():
    db = Session(Result(rows=[row()]), Result(scalar=42), Result(rows=[row()]))
    await call_list(db, rank="sp.", kingdom="Plantae")
    response = await call_list(db, rank="sp.", kingdom="Plantae", offset=50)
    assert len(db.calls) == 3
    assert response.pagination.total == 42


@pytest.mark.asyncio
async def test_prefix_and_query_are_escaped_and_prefix_is_index_friendly():
    db = Session(Result(rows=[]), Result(scalar=0))
    await call_list(db, prefix="Ab_", q="50%")
    sql, params = db.calls[0]
    assert "lower(canonical_name) LIKE :prefix_pattern" in sql
    assert params["prefix_pattern"] == "ab\\_%"
    assert params["q_pattern"] == "%50\\%%"


@pytest.mark.asyncio
async def test_popular_sort_orders_by_stored_observation_count():
    db = Session(Result(rows=[row()]), Result(scalar=1))
    await call_list(db, order_by="observations_count", order="desc")
    assert "metadata->>'observations_count'" in db.calls[0][0]


@pytest.mark.asyncio
async def test_core_page_failure_falls_back_to_legacy_queries():
    db = Session(RuntimeError("boom"), Result(rows=[row()]), Result(scalar=7))
    response = await call_list(db, rank="species")
    assert db.rollbacks == 1
    assert "bio.taxon_full" in db.calls[1][0]
    assert response.pagination.total == 7


@pytest.mark.asyncio
async def test_stats_reports_real_grouped_counts_and_caches():
    db = Session(
        Result(rows=[{"kingdom": "Fungi", "species": 406_534}, {"kingdom": "Plantae", "species": 10}]),
        Result(rows=[{"source": "mycobank", "species": 406_523}]),
        Result(rows=[{"source": "gbif", "species": 30_604}]),
        Result(scalar=577_481),
    )
    payload = await route.taxa_stats(db=db)
    assert payload["species_total"] == 406_544
    assert payload["taxa_total"] == 577_481
    assert payload["by_linked_source"] == [{"source": "gbif", "species": 30_604}]
    assert await route.taxa_stats(db=db) is payload
    assert len(db.calls) == 4


@pytest.mark.asyncio
async def test_kingdom_filter_resolves_undesignated_imports_and_accepts_csv():
    db = Session(Result(rows=[row()]), Result(scalar=11))
    await call_list(db, rank="species,sp.", kingdom="Protozoa,Chromista")
    sql, params = db.calls[0]
    assert params["rank_variants"] == ["species", "sp."]
    assert params["kingdom_0"] == "Protozoa" and params["kingdom_1"] == "Chromista"
    assert "kingdom IN (:kingdom_0, :kingdom_1)" in sql
    assert "metadata->>'kingdom'" in sql and "iconic_taxon_name" in sql


@pytest.mark.asyncio
async def test_kingdom_all_is_unfiltered():
    db = Session(Result(rows=[]), Result(scalar=0))
    await call_list(db, kingdom="all")
    assert not any(key.startswith("kingdom_") for key in db.calls[0][1])


@pytest.mark.asyncio
async def test_kingdom_counts_group_by_effective_kingdom_and_cache():
    db = Session(Result(rows=[{"kingdom": "Fungi", "taxon_count": 441_280}, {"kingdom": "Animalia", "taxon_count": 6_858}]))
    payload = await route.taxa_kingdom_counts(rank="species", db=db)
    sql, params = db.calls[0]
    assert params["rank_variants"] == ["species", "sp."]
    assert "metadata->>'kingdom'" in sql and "GROUP BY 1" in sql
    assert payload["total"] == 448_138
    assert await route.taxa_kingdom_counts(rank="sp.", db=db) is not None
    assert await route.taxa_kingdom_counts(rank="species", db=db) is payload
