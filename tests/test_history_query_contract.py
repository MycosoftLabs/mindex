"""Offline contract tests; database schema/runtime qualification is a separate gate."""
import asyncio
import inspect
import socket
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from mindex_api import cache as cache_module
from mindex_api.history_query import parse_history_window, history_sql
from mindex_api.routers import unified_search as search

DEFAULTS = dict(q="oak", types="observations", limit=2, lat=None, lng=None, radius=100,
                toxicity=None, kingdom=None, facility_type=None, since=None, until=None, session=None)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original_connect = socket.socket.connect
    def guarded_connect(sock, address):
        # Windows asyncio builds its private wakeup pipe with socketpair's loopback pair.
        caller = inspect.currentframe().f_back
        if caller.f_code is getattr(socket, "_fallback_socketpair", lambda: None).__code__:
            return original_connect(sock, address)
        pytest.fail("Canonical contract tests must not open a network connection")
    def forbidden(*args, **kwargs):
        pytest.fail("Canonical contract tests must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    cache_module._lru_cache.clear()
    cache_module._lru_timestamps.clear()
    cache = cache_module.RedisCache()
    cache._redis_url = ""
    monkeypatch.setattr(cache_module, "get_cache", lambda: cache)
    yield
    cache_module._lru_cache.clear()
    cache_module._lru_timestamps.clear()


@pytest.mark.parametrize("value", ["", "2026-10-01", "2026-10-01T00:00:00", "bad", "2026-02-30T00:00:00Z"])
@pytest.mark.parametrize("field", ["since", "until"])
def test_ambiguous_or_invalid_times_rejected(value, field):
    with pytest.raises(HTTPException) as caught:
        parse_history_window(**{"since": None, "until": None, field: value})
    assert caught.value.status_code == 422
    assert caught.value.detail == {"code": "invalid_history_time", "field": field,
        "message": "Use an ISO datetime with explicit UTC offset; date-only values are ambiguous."}


def test_offsets_normalized_and_open_bounds_supported():
    window = parse_history_window("2026-10-01T01:00:00+01:00", "2026-10-01T02:00:00+01:00")
    assert window.since == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert window.filters()["until"] == "2026-10-01T01:00:00+00:00"
    assert parse_history_window(None, "2026-10-01T00:00:00Z").since is None
    assert parse_history_window("2026-10-01T00:00:00Z", None).until is None


@pytest.mark.parametrize("end", ["2026-10-01T00:00:00Z", "2026-09-30T23:00:00Z"])
def test_equal_and_reversed_range_rejected(end):
    with pytest.raises(HTTPException, match="invalid_history_range"):
        parse_history_window("2026-10-01T00:00:00Z", end)


@pytest.mark.parametrize("domain,alias,table", [("observations", "o", "obs.observation"),
                                                  ("crep_entities", "e", "crep.unified_entities")])
def test_real_sql_predicates_precede_limit_and_location_is_conjunctive(domain, alias, table):
    window = parse_history_window("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    sql, params = history_sql(domain, "x' OR TRUE --", 1, window, 0, 0, 1)
    assert table in sql
    assert sql.index(f"{alias}.observed_at >= :since") < sql.index("ORDER BY") < sql.index("LIMIT")
    assert f"{alias}.observed_at < :until" in sql
    assert "AND ST_DWithin" in sql
    assert "x' OR TRUE" not in sql
    assert params["query"] == "%x' OR TRUE --%"
    assert params["since"].tzinfo == timezone.utc
    assert params["radius_m"] == 1000


def test_half_open_predicates_with_sqlite_boundary_fixture():
    # Execute the generated temporal predicate in a real SQL engine, not a Python filter.
    # This does not qualify PostgreSQL/PostGIS schema or query performance.
    import sqlite3
    window = parse_history_window("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    sql, params = history_sql("crep_entities", "oak", 1, window, None, None, None)
    predicate = sql.split(" WHERE ")[1].split(" AND (e.entity_type")[0]
    db = sqlite3.connect(":memory:")
    try:
        db.execute("CREATE TABLE events(id TEXT, observed_at TEXT)")
        db.executemany("INSERT INTO events VALUES (?, ?)", [
            ("before", "2026-09-30T23:59:59+00:00"), ("start", "2026-10-01T00:00:00+00:00"),
            ("inside", "2026-10-01T12:00:00+00:00"), ("end", "2026-10-02T00:00:00+00:00")])
        bound = {k: v.isoformat() for k, v in params.items() if k in ("since", "until")}
        assert db.execute(f"SELECT id FROM events e WHERE {predicate} ORDER BY observed_at DESC LIMIT 1", bound).fetchall() == [("inside",)]
        assert db.execute(f"SELECT id FROM events e WHERE {predicate} ORDER BY observed_at", bound).fetchall() == [("start",), ("inside",)]
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides,code", [
    ({"types": "taxa"}, "history_domain_unsupported"),
    ({"types": "all"}, "history_domain_unsupported"),
    ({"types": "observations,misspelled"}, "unknown_search_domain"),
    ({"kingdom": "Fungi"}, "history_filter_unsupported"),
    ({"lat": 0}, "incomplete_location"),
    ({"lat": 91, "lng": 0}, "invalid_location"),
    ({"lat": 0, "lng": 0, "radius": -1}, "invalid_radius"),
])
async def test_unsupported_contract_fails_before_any_query(monkeypatch, overrides, code):
    async def forbidden(*args):
        pytest.fail("Invalid history request must not execute a query")
    monkeypatch.setattr(search, "_safe_query", forbidden)
    with pytest.raises(HTTPException) as caught:
        await search.unified_search(**{**DEFAULTS, "since": "2026-10-01T00:00:00Z", **overrides})
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == code


@pytest.mark.asyncio
async def test_history_bypasses_cache_and_reports_unknown_coverage(monkeypatch):
    calls = []
    async def execute(session, sql, params, domain):
        calls.append((sql, params))
        return []
    class ForbiddenCache:
        def __getattr__(self, name):
            pytest.fail("Historical request must bypass cache methods")
    monkeypatch.setattr(cache_module, "get_cache", lambda: ForbiddenCache())
    monkeypatch.setattr(search, "_safe_query", execute)
    response = await search.unified_search(**{**DEFAULTS, "since": "2026-10-01T00:00:00Z"})
    assert len(calls) == 1
    assert response.results == {"observations": []}
    assert response.coverage["completeness"] == "unverified"
    assert response.coverage["ingestion_watermark"] is None
    assert response.coverage["external_fallback"] is False
    assert response.coverage["queried_tables"] == {"observations": "obs.observation"}


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [{"id": "retained-record"}]])
async def test_current_hit_and_miss_never_launch_acquisition(monkeypatch, rows):
    import sys
    def forbidden(*args, **kwargs):
        pytest.fail("Read path launched provider/cloud/agent work")
    monkeypatch.setitem(sys.modules, "mindex_api.scrape_pipeline", SimpleNamespace(LIVE_SCRAPERS={"taxa": forbidden}))
    monkeypatch.setitem(sys.modules, "mindex_api.supabase_client", SimpleNamespace(get_supabase=forbidden))
    monkeypatch.setitem(sys.modules, "mindex_api.utils.deep_agent_events", SimpleNamespace(schedule_domain_event=forbidden))
    async def query():
        return rows
    monkeypatch.setattr(search, "_build_dispatch", lambda *args: {"taxa": query})
    response = await search.unified_search(**{**DEFAULTS, "types": "taxa"})
    await asyncio.sleep(0)
    assert response.results["taxa"] == rows
    assert response.coverage["external_fallback"] is False
    # Also prevent a module-level alias from escaping the runtime traps above.
    source = inspect.getsource(search)
    assert "schedule_domain_event(" not in source
    assert "create_task(" not in source
    assert "LIVE_SCRAPERS" not in source


@pytest.mark.asyncio
async def test_old_live_capable_cache_namespace_is_not_reused(monkeypatch):
    cache = cache_module.get_cache()
    options = {k: DEFAULTS[k] for k in ("limit", "lat", "lng", "radius", "toxicity", "kingdom", "facility_type", "since", "until")}
    await cache.cache_search("oak", "taxa", {"results": {"taxa": [{"id": "old-live-result"}]}}, options=options)
    async def query():
        return []
    monkeypatch.setattr(search, "_build_dispatch", lambda *args: {"taxa": query})
    assert (await search.unified_search(**{**DEFAULTS, "types": "taxa"})).results == {"taxa": []}
