"""Actual router/domain functions against a strict offline session contract."""
import asyncio
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]


class Cache:
    async def connect(self):
        pass

    async def get_cached_search(self, *args, **kwargs):
        return None

    async def cache_search(self, *args, **kwargs):
        pass


@pytest.fixture
def route(monkeypatch):
    def stub(name, **attrs):
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    stub("mindex_api.dependencies", get_db_session=lambda: None)
    stub("mindex_api.cache", get_cache=lambda: Cache())
    stub("mindex_api.scrape_pipeline", LIVE_SCRAPERS={})
    stub("mindex_api.supabase_client", get_supabase=lambda: SimpleNamespace(enabled=False))
    stub("mindex_api.utils.deep_agent_events", schedule_domain_event=lambda **kwargs: None)
    stub("mindex_api.services.ancestry_public_members", search_public_fungip=None)
    name = "mindex_api.routers.session_contract_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "mindex_api/routers/unified_search.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class StrictSession:
    def __init__(self, fail=None, rollback_fails=False, empty=False):
        self.active = False
        self.aborted = False
        self.fail = fail
        self.rollback_fails = rollback_fails
        self.empty = empty
        self.events = []
        self.overlaps = 0
        self.aborted_reuses = 0

    def begin_nested(self):
        return nullcontext()

    async def execute(self, statement, params=None):
        sql = str(statement)
        domain = "store" if "INSERT INTO" in sql else "aircraft" if "FROM transport.aircraft" in sql else "vessels"
        if self.active:
            self.overlaps += 1
            raise RuntimeError("concurrent AsyncSession operation")
        if self.aborted:
            self.aborted_reuses += 1
            raise RuntimeError("current transaction is aborted")
        self.active = True
        self.events.append("start:" + domain)
        try:
            # Deterministically gives an incorrectly concurrent caller a turn.
            await asyncio.sleep(0)
            if self.fail == domain:
                self.aborted = True
                self.fail = None
                raise RuntimeError("synthetic query failure")
            item = SimpleNamespace(
                id=domain + "-1", source="fixture", icao24="abc123", callsign="Fixture",
                registration=None, aircraft_type=None, origin=None, destination=None,
                altitude_ft=0, ground_speed_kts=0, heading=0, observed_at="2026-10-02",
                lat=0.0, lng=0.0, mmsi="111111111", imo=None, name="Fixture vessel",
                vessel_type=None, flag=None, speed_knots=0, course=0, nav_status=None,
            )
            return SimpleNamespace(fetchall=lambda: [] if self.empty else [item])
        finally:
            self.active = False
            self.events.append("end:" + domain)

    async def rollback(self):
        assert not self.active, "rollback raced an active query"
        self.events.append("rollback")
        if self.rollback_fails:
            raise RuntimeError("synthetic rollback failure")
        self.aborted = False

    async def commit(self):
        assert not self.active and not self.aborted
        self.events.append("commit")
        await asyncio.sleep(0)


def invoke(route, session, endpoint="unified", **overrides):
    args = dict(q="fixture", types="aircraft,vessels", limit=2, lat=None, lng=None,
                radius=100, toxicity=None, kingdom=None, facility_type=None, session=session)
    if endpoint == "unified":
        args.update(read_only=True, since=None, until=None)
        args.update(overrides)
        return asyncio.run(route.unified_search(**args))
    if endpoint == "earth":
        args.update(overrides)
        return asyncio.run(route.earth_search(**args))
    return asyncio.run(route.search_nearby(lat=0, lng=0, radius=10,
                                         types="aircraft,vessels", limit=2, session=session))


@pytest.mark.parametrize("endpoint", ["unified", "earth", "nearby"])
def test_actual_selected_domains_never_share_session_concurrently(route, endpoint):
    session = StrictSession()
    response = invoke(route, session, endpoint)
    assert session.overlaps == 0
    assert session.events == ["start:aircraft", "end:aircraft", "start:vessels", "end:vessels"]
    results = response["results"] if isinstance(response, dict) else response.results
    assert len(results["aircraft"]) == len(results["vessels"]) == 1


def test_unselected_domain_coroutine_is_never_created(route, monkeypatch):
    created = []
    original = route.search_taxa

    def observe_creation(*args, **kwargs):
        created.append("taxa")
        return original(*args, **kwargs)

    monkeypatch.setattr(route, "search_taxa", observe_creation)
    invoke(route, StrictSession())
    assert created == []


def test_failed_query_rolls_back_before_next_real_domain(route):
    session = StrictSession(fail="aircraft")
    with pytest.raises(HTTPException) as failed:
        invoke(route, session)
    assert failed.value.status_code == 503
    assert session.events == ["start:aircraft", "end:aircraft", "rollback", "start:vessels", "end:vessels"]
    assert session.aborted_reuses == session.overlaps == 0
    assert failed.value.detail["domain_errors"] == {"aircraft": {"code": "domain_unavailable"}}
    assert "aircraft" not in failed.value.detail["results"]
    assert len(failed.value.detail["results"]["vessels"]) == 1


def test_failed_rollback_prevents_next_query(route):
    session = StrictSession(fail="aircraft", rollback_fails=True)
    with pytest.raises(RuntimeError, match="session recovery failed"):
        invoke(route, session)
    assert "start:vessels" not in session.events


@pytest.mark.parametrize("read_only", [True, False])
def test_scraped_writes_are_awaited_and_guarded(route, read_only):
    scraper = sys.modules["mindex_api.scrape_pipeline"]
    scraper.LIVE_SCRAPERS = {
        name: lambda query, name=name: [{"id": name, "lat": 0, "lng": 0}]
        for name in ("aircraft", "vessels")
    }
    session = StrictSession(empty=True)
    response = invoke(route, session, read_only=read_only)
    assert response.total_count == 2
    writes = session.events.count("start:store")
    assert writes == (0 if read_only else 2)
    assert session.events.count("commit") == writes
    assert session.overlaps == 0


def test_successful_empty_query_is_distinct_from_query_failure(route):
    response = invoke(route, StrictSession(empty=True))
    assert response.results == {"aircraft": [], "vessels": []}
    assert response.domain_availability == {}


def test_cancellation_recovers_without_starting_or_creating_next_operation(route, monkeypatch):
    session = StrictSession()
    created = []

    async def cancelled(*args, **kwargs):
        session.aborted = True
        raise asyncio.CancelledError

    def next_operation(*args, **kwargs):
        created.append("vessels")
        raise AssertionError("cancelled request created the next domain")

    monkeypatch.setattr(route, "search_aircraft", cancelled)
    monkeypatch.setattr(route, "search_vessels", next_operation)
    with pytest.raises(asyncio.CancelledError):
        invoke(route, session)
    assert session.events == ["rollback"]
    assert not session.aborted and not created


def test_domain_projection_exception_recovers_before_next_query(route, monkeypatch):
    session = StrictSession()

    async def invalid_projection(*args, **kwargs):
        raise ValueError("synthetic DTO failure")

    monkeypatch.setattr(route, "search_aircraft", invalid_projection)
    with pytest.raises(HTTPException) as failed:
        invoke(route, session)
    assert failed.value.status_code == 503
    assert session.events == ["rollback", "start:vessels", "end:vessels"]
    assert failed.value.detail["domain_errors"] == {"aircraft": {"code": "domain_unavailable"}}
    assert len(failed.value.detail["results"]["vessels"]) == 1


def test_failed_scraped_write_recovers_before_the_next_store(route):
    sys.modules["mindex_api.scrape_pipeline"].LIVE_SCRAPERS = {
        name: lambda query, name=name: [{"id": name, "lat": 0, "lng": 0}]
        for name in ("aircraft", "vessels")
    }
    session = StrictSession(empty=True, fail="store")
    response = invoke(route, session, read_only=False)
    assert response.total_count == 2
    assert session.events[-6:] == ["start:store", "end:store", "rollback", "start:store", "end:store", "commit"]
    assert session.aborted_reuses == session.overlaps == 0


def test_optional_fungip_error_recovered_before_request_owned_store(route):
    sys.modules["mindex_api.scrape_pipeline"].LIVE_SCRAPERS = {
        "aircraft": lambda query: [{"id": "scraped", "lat": 0, "lng": 0}],
    }
    session = StrictSession(empty=True)

    async def optional_index(db, *args, **kwargs):
        assert not db.active
        db.aborted = True
        return [], route.FungiPIndexAvailability(status="error", reason="query_failed")

    route.search_public_fungip = optional_index
    response = invoke(route, session, types="aircraft,fungip", read_only=False)
    assert response.domain_availability["fungip"]["status"] == "error"
    assert session.events == ["start:aircraft", "end:aircraft", "rollback", "start:store", "end:store", "commit"]
    assert session.aborted_reuses == session.overlaps == 0


@pytest.mark.parametrize("failed", [False, True])
def test_actual_rag_path_uses_same_sequential_recovery(route, monkeypatch, failed):
    monkeypatch.setitem(sys.modules, "mindex_api.routers.unified_search", route)
    name = "mindex_api.routers.rag_session_contract_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "mindex_api/routers/rag_retrieve.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    session = StrictSession(fail="aircraft" if failed else None)
    response = asyncio.run(module.rag_retrieve(
        module.RAGRetrieveRequest(query="fixture", types="aircraft,vessels", limit=2), session=session,
    ))
    assert session.overlaps == session.aborted_reuses == 0
    assert session.events.count("rollback") == int(failed)
    assert response.total_chunks == (1 if failed else 2)
    assert response.chunks[-1].source_id == "vessels-1"
