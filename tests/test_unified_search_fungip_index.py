"""Offline compatibility tests for MINDEX unified-search FungiP results."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from mindex_api.contracts.v1.ancestry_index import FungiPIndexAvailability
from mindex_api.routers import unified_search as search_router


class _Cache:
    def __init__(self, value):
        self.value = value
        self.writes = []

    async def connect(self):
        return None

    async def get_cached_search(self, query, types, **kwargs):
        return self.value

    async def cache_search(self, query, types, value, ttl, **kwargs):
        self.writes.append((query, types, value, ttl))


class _DbResult:
    def __init__(self, one=None):
        self._one = one

    def mappings(self):
        return self

    def one(self):
        return self._one


class _OverlapRejectingSession:
    def __init__(self):
        self.primary_active = False
        self.overlap = False
        self.rollback_during_primary = False
        self.rollback_calls = 0
        self.sql_calls = 0

    async def execute(self, statement, params=None):
        if self.primary_active:
            self.overlap = True
            raise AssertionError("shared AsyncSession used while primary domain query is active")
        self.sql_calls += 1
        if "to_regclass" in str(statement):
            return _DbResult({
                "species_table": "fungip.species", "launch_table": None, "batch_table": None,
            })
        raise RuntimeError("synthetic optional FungiP query failure")

    async def rollback(self):
        self.rollback_calls += 1
        if self.primary_active:
            self.rollback_during_primary = True
            raise AssertionError("optional index rollback raced with primary domain query")


@pytest.mark.asyncio
async def test_old_biological_cache_is_rebuilt_as_a_coherent_response(monkeypatch):
    cache = _Cache({
        "domains_searched": ["taxa", "species", "compounds", "genetics", "observations"],
        "results": {
            "taxa": [{"id": "old-taxon"}], "species": [{"id": "old-species"}],
            "compounds": [{"id": "old-compound"}], "genetics": [{"id": "old-genetic"}],
            "observations": [{"id": "old-observation"}],
            "fungip": [{"id": "fungip:old"}],
        },
        "domain_availability": {"fungip": {"status": "available"}},
        "total_count": 6,
        "filters_applied": {},
    })
    monkeypatch.setattr("mindex_api.cache.get_cache", lambda: cache)
    current = {
        "taxa": [{"id": "new-taxon"}], "species": [{"id": "new-species"}],
        "compounds": [{"id": "new-compound"}], "genetics": [{"id": "new-genetic"}],
        "observations": [{"id": "new-observation"}],
    }
    async def completed(value):
        return value

    monkeypatch.setattr(search_router, "_build_dispatch", lambda *_args: {
        name: completed(value) for name, value in current.items()
    })
    class _Supabase:
        enabled = False

    monkeypatch.setattr("mindex_api.supabase_client.get_supabase", lambda: _Supabase())
    monkeypatch.setattr(search_router, "schedule_domain_event", lambda **_kwargs: None)
    calls = []

    async def search_source(_session, query, limit, kingdom=None):
        calls.append((query, limit, kingdom))
        return ([{"id": "fungip:FG026", "domain": "fungip", "properties": {"species_id": "FG026"}}],
                FungiPIndexAvailability(status="available"))

    monkeypatch.setattr(search_router, "search_public_fungip", search_source)
    response = await search_router.unified_search(
        q="mushroom", types="biological", limit=30, lat=None, lng=None, radius=100,
        toxicity=None, kingdom="Fungi", facility_type=None, since=None, until=None, session=object(),
    )

    assert calls == [("mushroom", 30, "Fungi")]
    assert response.domains_searched == ["taxa", "species", "fungip", "compounds", "genetics", "observations"]
    assert response.results["taxa"] == current["taxa"]
    assert response.results["species"] == current["species"]
    assert "old-taxon" not in str(response.results)
    assert response.results["fungip"][0]["id"] == "fungip:FG026"
    assert response.total_count == 6
    assert response.domain_availability["fungip"]["status"] == "available"
    assert response.filters_applied["kingdom"] == "Fungi"
    assert cache.writes[0][3] == 120


@pytest.mark.asyncio
async def test_cached_nontarget_domain_does_not_trigger_fungip_query(monkeypatch):
    cache = _Cache({
        "domains_searched": ["taxa"],
        "results": {"taxa": [{"id": "42"}]},
        "total_count": 1,
        "filters_applied": {},
    })
    monkeypatch.setattr("mindex_api.cache.get_cache", lambda: cache)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("FungiP should not be searched when not requested")

    monkeypatch.setattr(search_router, "search_public_fungip", forbidden)
    response = await search_router.unified_search(
        q="Agaricus", types="taxa", limit=20, lat=None, lng=None, radius=100,
        toxicity=None, kingdom=None, facility_type=None, since=None, until=None, session=object(),
    )
    assert response.results["taxa"] == [{"id": "42"}]
    assert "fungip" not in response.results
    assert cache.writes == []


@pytest.mark.asyncio
async def test_fungip_cache_refreshes_when_limit_or_material_filter_differs(monkeypatch):
    old_context = search_router._fungip_search_context(
        "Agaricus", 30, None, None, 100, None, None, None, None, None,
    )
    cache = _Cache({
        "domains_searched": ["fungip"],
        "results": {"fungip": [{"id": "fungip:FG026"}, {"id": "fungip:FG027"}]},
        "domain_availability": {"fungip": {"status": "available"}},
        "fungip_search_context": old_context,
        "total_count": 2,
        "filters_applied": {},
    })
    monkeypatch.setattr("mindex_api.cache.get_cache", lambda: cache)
    class _Supabase:
        enabled = False

    monkeypatch.setattr("mindex_api.supabase_client.get_supabase", lambda: _Supabase())
    monkeypatch.setattr(search_router, "schedule_domain_event", lambda **_kwargs: None)
    calls = []

    async def search_source(_session, query, limit, kingdom=None):
        calls.append((query, limit, kingdom))
        return ([{"id": "fungip:FG026"}], FungiPIndexAvailability(status="available"))

    monkeypatch.setattr(search_router, "search_public_fungip", search_source)
    monkeypatch.setattr(search_router, "_build_dispatch", lambda *_args: {})
    response = await search_router.unified_search(
        q="Agaricus", types="fungip", limit=1, lat=None, lng=None, radius=100,
        toxicity=None, kingdom="Fungi", facility_type=None, since=None, until=None, session=object(),
    )
    assert calls == [("Agaricus", 1, "Fungi")]
    assert response.results["fungip"] == [{"id": "fungip:FG026"}]
    assert response.total_count == 1
    assert cache.writes[0][2]["fungip_search_context"]["limit"] == 1
    assert cache.writes[0][2]["fungip_search_context"]["kingdom"] == "Fungi"
    assert response.filters_applied["kingdom"] == "Fungi"


@pytest.mark.asyncio
async def test_changed_kingdom_bypasses_mixed_cache_and_rebuilds_results_and_filters(monkeypatch):
    cached_context = search_router._fungip_search_context(
        "Agaricus", 20, None, None, 100, None, "Fungi", None, None, None,
    )
    cache = _Cache({
        "domains_searched": ["taxa", "fungip"],
        "results": {"taxa": [{"id": "fungal-core"}], "fungip": [{"id": "fungip:FG026"}]},
        "domain_availability": {"fungip": {"status": "available"}},
        "fungip_search_context": cached_context,
        "total_count": 2,
        "filters_applied": {"kingdom": "Fungi"},
    })
    monkeypatch.setattr("mindex_api.cache.get_cache", lambda: cache)
    dispatched = []

    async def plant_taxa(*_args):
        return [{"id": "plant-core"}]

    def build_dispatch(*args):
        dispatched.append(args[7])
        return {"taxa": plant_taxa()}

    monkeypatch.setattr(search_router, "_build_dispatch", build_dispatch)
    class _Supabase:
        enabled = False

    monkeypatch.setattr("mindex_api.supabase_client.get_supabase", lambda: _Supabase())
    monkeypatch.setattr(search_router, "schedule_domain_event", lambda **_kwargs: None)
    response = await search_router.unified_search(
        q="Agaricus", types="taxa,fungip", limit=20, lat=None, lng=None, radius=100,
        toxicity=None, kingdom="Plantae", facility_type=None, since=None, until=None, session=object(),
    )

    assert dispatched == ["Plantae"]
    assert response.results == {"taxa": [{"id": "plant-core"}], "fungip": []}
    assert response.total_count == 1
    assert response.filters_applied["kingdom"] == "Plantae"
    assert response.domain_availability["fungip"]["status"] == "available"
    assert cache.writes[0][2]["results"] == response.results
    assert cache.writes[0][2]["filters_applied"]["kingdom"] == "Plantae"


@pytest.mark.asyncio
async def test_matching_fungip_cache_context_remains_a_cache_hit(monkeypatch):
    context = search_router._fungip_search_context(
        "Agaricus", 20, None, None, 100, None, "Fungi", None, None, None,
    )
    results = [{"id": "fungip:FG026"}]
    cache = _Cache({
        "domains_searched": ["fungip"], "results": {"fungip": results},
        "domain_availability": {"fungip": {"status": "available"}},
        "fungip_search_context": context, "total_count": 1,
        "filters_applied": {"kingdom": "Fungi"},
    })
    monkeypatch.setattr("mindex_api.cache.get_cache", lambda: cache)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Matching FungiP cache should not trigger a source query")

    monkeypatch.setattr(search_router, "search_public_fungip", forbidden)
    response = await search_router.unified_search(
        q="Agaricus", types="fungip", limit=20, lat=None, lng=None, radius=100,
        toxicity=None, kingdom="Fungi", facility_type=None, since=None, until=None, session=object(),
    )
    assert response.results["fungip"] == results
    assert response.filters_applied["kingdom"] == "Fungi"
    assert cache.writes == []


def test_life_and_biological_aliases_include_separate_fungip_domain():
    assert "fungip" in search_router._resolve_domains("life")
    assert "fungip" in search_router._resolve_domains("biological")
    assert search_router._resolve_domains("fungip") == ["fungip"]


@pytest.mark.asyncio
async def test_cold_fungip_failure_runs_after_primary_gather_and_preserves_results(monkeypatch):
    cache = _Cache(None)
    monkeypatch.setattr("mindex_api.cache.get_cache", lambda: cache)
    session = _OverlapRejectingSession()

    async def primary_search(db, *_args):
        db.primary_active = True
        await asyncio.sleep(0.02)
        db.primary_active = False
        return [{"id": "core:taxon-1"}]

    monkeypatch.setattr(search_router, "_build_dispatch", lambda *_args: {"taxa": primary_search(session)})

    class _Supabase:
        enabled = False

    monkeypatch.setattr("mindex_api.supabase_client.get_supabase", lambda: _Supabase())
    monkeypatch.setattr(search_router, "schedule_domain_event", lambda **_kwargs: None)
    response = await search_router.unified_search(
        q="Agaricus", types="taxa,fungip", limit=10, lat=None, lng=None, radius=100,
        toxicity=None, kingdom=None, facility_type=None, since=None, until=None, session=session,
    )

    assert not session.overlap
    assert not session.rollback_during_primary
    assert session.rollback_calls == 1
    assert response.results["taxa"] == [{"id": "core:taxon-1"}]
    assert response.results["fungip"] == []
    assert response.total_count == 1
    assert response.domain_availability["fungip"] == {"status": "error", "reason": "query_failed"}


@pytest.mark.asyncio
async def test_taxon_search_keeps_uuid_identity_as_string_with_explicit_uuid_field(monkeypatch):
    taxon_id = UUID("6eb9e962-05c3-4f2b-8ed1-598cbb2a0ac2")

    class Row:
        id = taxon_id
        canonical_name = "Agaricus exampleus"
        common_name = None
        rank = "species"
        description = None
        image_url = None
        observation_count = 0
        toxicity = None
        edibility = None

    async def safe_query(*_args):
        return [Row()]

    monkeypatch.setattr(search_router, "_safe_query", safe_query)
    results = await search_router.search_taxa(object(), "Agaricus", 10)
    assert results[0]["id"] == str(taxon_id)
    assert results[0]["mindex_uuid"] == str(taxon_id)
