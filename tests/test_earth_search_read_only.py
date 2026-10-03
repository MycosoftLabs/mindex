"""Actual Earth search handler/domain projection with fake SQL and event sinks."""
import asyncio
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
OBSERVATION = "3050d365-d401-40f8-abb2-18ef1cbdf345"
TAXON = "8948f374-9439-4165-b592-33e3bf9a8998"
OMITTED = object()


@pytest.fixture
def route(monkeypatch):
    def stub(name, **values):
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)

    stub("mindex_api.dependencies", get_db_session=lambda: None)
    stub("mindex_api.services.ancestry_public_members", search_public_fungip=None)
    stub("mindex_api.utils.deep_agent_events", schedule_domain_event=lambda **kwargs: None)
    name = "mindex_api.routers.earth_read_only_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "mindex_api/routers/unified_search.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    # Keep comparison deterministic without changing runtime code.
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: 10.0))
    return module


class Session:
    def __init__(self, empty=False):
        self.empty = empty
        self.calls = []

    def begin_nested(self):
        return nullcontext()

    async def execute(self, sql, params):
        statement = str(sql)
        assert statement.lstrip().startswith("SELECT")
        self.calls.append((statement, dict(params)))
        if "FROM obs.observation o" in statement and not self.empty:
            rows = [SimpleNamespace(id=OBSERVATION, taxon_id=TAXON,
                    taxon_name="Agaricus campestris", location=None,
                    lat=37.439678, lng=-122.165886, observed_at="2020-01-02T00:00:00Z",
                    image_url=None, source="gbif")]
        else:
            assert "FROM species.sightings s" in statement or self.empty
            rows = []
        return SimpleNamespace(fetchall=lambda: rows)


def invoke(route, session, read_only=OMITTED):
    args = dict(q="Agaricus", types="observations", limit=2, lat=None, lng=None,
                radius=100, toxicity=None, kingdom=None, facility_type=None, session=session)
    if read_only is not OMITTED:
        args["read_only"] = read_only
    return asyncio.run(route.earth_search(**args))


def test_earth_query_contract_exposes_opt_in_false_default(route):
    endpoint = next(r for r in route.router.routes if r.endpoint is route.earth_search)
    flag = next(p for p in endpoint.dependant.query_params if p.name == "read_only")
    assert flag.default is False
    for raw, expected in [("true", True), ("false", False)]:
        parsed, error = flag.validate(raw, {}, loc=("query", "read_only"))
        assert error is None and parsed is expected
    _, error = flag.validate("not-a-bool", {}, loc=("query", "read_only"))
    assert error is not None


@pytest.mark.parametrize("flag", [OMITTED, False])
def test_default_and_explicit_false_preserve_existing_event(route, monkeypatch, flag):
    events = []
    monkeypatch.setattr(route, "schedule_domain_event", lambda **kwargs: events.append(kwargs))
    response = invoke(route, Session(), flag)
    assert response.total_count == 1
    assert len(events) == 1
    assert events[0]["context"]["route"] == "/unified-search/earth"
    assert events[0]["context"]["domains_searched"] == ["observations"]
    assert events[0]["context"]["total_count"] == 1


def test_read_only_does_not_call_raising_event_sink(route, monkeypatch):
    def forbidden(**_):
        raise AssertionError("read-only Earth search attempted an event")
    monkeypatch.setattr(route, "schedule_domain_event", forbidden)
    session = Session()
    response = invoke(route, session, True)
    assert response.results["observations"][0]["id"] == OBSERVATION
    assert response.results["observations"][0]["taxon_id"] == TAXON
    assert response.universal_results[0].properties["taxon_id"] == TAXON
    assert (response.universal_results[0].lat, response.universal_results[0].lng) == (37.439678, -122.165886)
    assert len(session.calls) == 2


def test_read_only_preserves_exact_response_and_query_parameters(route):
    before, after = Session(), Session()
    ordinary = invoke(route, before)
    read_only = invoke(route, after, True)
    assert ordinary.model_dump() == read_only.model_dump()
    assert before.calls == after.calls
    assert all(params["limit"] == 2 and params["query"] == "%Agaricus%" for _, params in after.calls)


@pytest.mark.parametrize("flag", [False, True])
def test_empty_result_shape_is_preserved_without_inventing_data(route, monkeypatch, flag):
    events = []
    monkeypatch.setattr(route, "schedule_domain_event", lambda **kwargs: events.append(kwargs))
    response = invoke(route, Session(empty=True), flag)
    assert response.results == {"observations": []}
    assert response.universal_results == [] and response.total_count == 0
    assert len(events) == (0 if flag else 1)
