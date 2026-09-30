"""Offline contract fixtures; no production records or external services."""

import asyncio
import sys
from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace

import pytest

from mindex_api import cache as cache_module
from mindex_api.routers import unified_search as search

ORIGINAL_DISPATCH = search._build_dispatch


DEFAULTS = dict(
    q="fixture oak", types="taxa", limit=20, lat=None, lng=None, radius=100,
    toxicity=None, kingdom=None, facility_type=None, since=None, until=None,
    session=None,
)


@pytest.fixture
def offline_search(monkeypatch):
    cache_module._lru_cache.clear()
    cache_module._lru_timestamps.clear()
    cache = cache_module.RedisCache()
    cache._redis_url = ""
    calls = []

    def dispatch(*args):
        async def selected():
            calls.append(args[1:])
            return [{"id": "fixture-only", "name": "Offline fixture"}]
        return {"taxa": selected}

    monkeypatch.setattr(cache_module, "get_cache", lambda: cache)
    monkeypatch.setattr(search, "_build_dispatch", dispatch)
    monkeypatch.setattr(search, "schedule_domain_event", lambda **kwargs: None)
    monkeypatch.setitem(sys.modules, "mindex_api.scrape_pipeline", SimpleNamespace(LIVE_SCRAPERS={}))
    monkeypatch.setitem(sys.modules, "mindex_api.supabase_client", SimpleNamespace(
        get_supabase=lambda: SimpleNamespace(enabled=False)))
    yield cache, calls
    cache_module._lru_cache.clear()
    cache_module._lru_timestamps.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("option,value", [
    ("limit", 1), ("lat", 0.0), ("lng", 0.0), ("radius", 1.0),
    ("toxicity", "edible"), ("kingdom", "Fungi"),
    ("facility_type", "dam"), ("since", "2026-01-01"), ("until", "2026-02-01"),
])
async def test_each_request_option_separates_cache_entries(offline_search, option, value):
    _, calls = offline_search
    await search.unified_search(**DEFAULTS)
    await search.unified_search(**{**DEFAULTS, option: value})
    assert len(calls) == 2, f"{option} must not reuse a differently filtered response"


@pytest.mark.asyncio
async def test_identical_request_hits_cache(offline_search):
    _, calls = offline_search
    first = await search.unified_search(**DEFAULTS)
    second = await search.unified_search(**DEFAULTS)
    assert len(calls) == 1
    assert first.results == second.results


@pytest.mark.asyncio
async def test_equivalent_ordered_domains_share_cache_entry(offline_search):
    _, calls = offline_search
    await search.unified_search(**DEFAULTS)
    await search.unified_search(**{**DEFAULTS, "types": " TAXA, taxa "})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_legacy_incomplete_key_is_not_read(offline_search):
    cache, calls = offline_search
    legacy = "search:" + cache_module._hash(DEFAULTS["q"] + "|taxa")
    await cache.set_json(legacy, {
        "domains_searched": ["taxa"], "results": {"taxa": [{"id": "wrong-place"}]},
        "total_count": 1, "filters_applied": {},
    })
    result = await search.unified_search(**DEFAULTS)
    assert len(calls) == 1
    assert result.results["taxa"][0]["id"] == "fixture-only"


class FixtureSession:
    """Deterministic query/savepoint protocol double, not a database engine."""
    def __init__(self, failures=(), empty=()):
        self.failures = failures
        self.empty = empty
        self.trace = []
        self.active = False

    def begin_nested(self):
        session = self

        class Savepoint(AbstractAsyncContextManager):
            async def __aenter__(self):
                assert not session.active, "shared session must never run concurrent queries"
                session.active = True
                session.trace.append("begin")
                return self

            async def __aexit__(self, typ, value, traceback):
                session.trace.append("rollback" if typ else "release")
                session.active = False
        return Savepoint()

    async def execute(self, statement, params):
        domain = params["domain"]
        self.trace.append(domain)
        if domain in self.failures:
            raise RuntimeError("fixture-private SQL connection detail must not escape")
        rows = [] if domain in self.empty else [{"id": "fixture-" + domain, "name": domain}]
        return SimpleNamespace(fetchall=lambda: rows)


@pytest.fixture
def domain_fixture(offline_search, monkeypatch):
    cache, _ = offline_search
    monkeypatch.setattr(search, "_build_dispatch", ORIGINAL_DISPATCH)
    created = []
    effects = []

    def domain_function(name):
        def create(session, *args):
            created.append(name)
            return search._safe_query(session, "SELECT fixture_only", {"domain": name}, name)
        return create

    for domain in search.ALL_DOMAINS:
        monkeypatch.setattr(search, "search_" + domain, domain_function(domain))

    def scrape(query):
        effects.append("scrape")
        return []

    async def persist(*args):
        effects.append("persist")

    async def cache_write(*args, **kwargs):
        effects.append("cache")

    async def sync(*args):
        effects.append("supabase")

    monkeypatch.setitem(sys.modules, "mindex_api.scrape_pipeline", SimpleNamespace(
        LIVE_SCRAPERS={domain: scrape for domain in search.ALL_DOMAINS}))
    monkeypatch.setitem(sys.modules, "mindex_api.supabase_client", SimpleNamespace(
        get_supabase=lambda: SimpleNamespace(enabled=True, sync_search_results=sync)))
    monkeypatch.setattr(search, "_async_store_scraped", persist)
    monkeypatch.setattr(cache, "cache_search", cache_write)
    monkeypatch.setattr(search, "schedule_domain_event", lambda **kw: effects.append("event"))
    return created, effects


def route_arguments(route, session, domains):
    args = {**DEFAULTS, "session": session, "types": domains}
    if route == "earth":
        args.pop("since")
        args.pop("until")
        return search.earth_search, args
    if route == "nearby":
        return search.search_nearby, dict(lat=0.0, lng=0.0, radius=1, types=domains, limit=20, session=session)
    return search.unified_search, args


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["unified", "earth", "nearby"])
@pytest.mark.parametrize("all_failed", [False, True])
async def test_failed_domain_is_explicit_and_has_no_side_effects(domain_fixture, route, all_failed):
    from fastapi import HTTPException

    created, effects = domain_fixture
    domains = ["earthquakes", "weather"] if route == "nearby" else ["taxa", "species"]
    session = FixtureSession(failures=domains if all_failed else domains[:1])
    fn, args = route_arguments(route, session, ",".join(domains))
    with pytest.raises(HTTPException) as caught:
        await fn(**args)
    await asyncio.sleep(0)  # expose any incorrectly scheduled background side effect
    assert caught.value.status_code == 503
    detail = caught.value.detail
    assert detail["status"] == ("unavailable" if all_failed else "partial")
    assert set(detail["domain_errors"]) == set(session.failures)
    assert detail["results"] == ({} if all_failed else {domains[1]: [{"id": "fixture-" + domains[1], "name": domains[1]}]})
    assert "fixture-private" not in str(detail)
    assert created == domains
    assert session.trace == ["begin", domains[0], "rollback", "begin", domains[1], "rollback" if all_failed else "release"]
    assert effects == []


@pytest.mark.asyncio
async def test_healthy_empty_is_distinct_from_failed_domain(domain_fixture):
    from fastapi import HTTPException

    session = FixtureSession(failures=["taxa"], empty=["species"])
    with pytest.raises(HTTPException) as caught:
        await search.unified_search(**{**DEFAULTS, "types": "taxa,species", "session": session})
    assert caught.value.detail["status"] == "partial"
    assert caught.value.detail["results"] == {"species": []}
    assert set(caught.value.detail["domain_errors"]) == {"taxa"}
    assert domain_fixture[1] == []


@pytest.mark.asyncio
async def test_safe_query_healthy_empty_releases_savepoint():
    session = FixtureSession(empty=["taxa"])
    assert await search._safe_query(session, "SELECT fixture_only", {"domain": "taxa"}, "taxa") == []
    assert session.trace == ["begin", "taxa", "release"]


def test_dispatch_is_lazy(domain_fixture):
    import inspect

    dispatch = search._build_dispatch(None, "fixture", 1, None, None, 1, None, None, None)
    # Close baseline coroutines in the red run rather than leaking them.
    coroutines = [value for value in dispatch.values() if inspect.iscoroutine(value)]
    for coroutine in coroutines:
        coroutine.close()
    assert not coroutines
    assert domain_fixture[0] == []


@pytest.mark.parametrize("include_healthy", [False, True])
def test_worldview_propagates_incomplete_search_before_governance(domain_fixture, monkeypatch, include_healthy):
    from datetime import datetime, timezone
    from uuid import uuid4
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mindex_api.auth import CallerIdentity, require_worldview_key
    from mindex_api.dependencies import get_db_session
    from mindex_api.routers.worldview import search as worldview

    async def forbidden_wrap(**kwargs):
        pytest.fail("Incomplete search must not be flattened or acquire snapshot/governance metadata")

    monkeypatch.setattr(worldview, "wrap_governed_response", forbidden_wrap)
    fixture_id = uuid4()
    fixture_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def typed_healthy(session, *args):
        rows = await search._safe_query(session, "SELECT fixture_only", {"domain": "species"}, "species")
        return [{**rows[0], "id": fixture_id, "observed_at": fixture_time}]

    monkeypatch.setattr(search, "search_species", typed_healthy)
    app = FastAPI()
    app.include_router(worldview.router, prefix="/api/worldview/v1")
    app.dependency_overrides[require_worldview_key] = lambda: CallerIdentity(uuid4(), uuid4(), "human", "pro")
    app.dependency_overrides[get_db_session] = lambda: FixtureSession(failures=["taxa"])
    with TestClient(app) as client:
        response = client.get("/api/worldview/v1/search", params={
            "q": "fixture", "domains": "taxa,species" if include_healthy else "taxa"})
    assert response.status_code == 503
    assert response.json()["detail"]["status"] == ("partial" if include_healthy else "unavailable")
    if include_healthy:
        row = response.json()["detail"]["results"]["species"][0]
        assert row["id"] == str(fixture_id)
        assert row["observed_at"] == fixture_time.isoformat()
    assert domain_fixture[1] == []
