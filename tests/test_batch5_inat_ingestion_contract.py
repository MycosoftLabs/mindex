"""Deterministic fixture contracts only: no API, DB, settings or ETL service startup."""

from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "_batch5_fixture_etl"


def _module(name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module
    return module


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def contract(monkeypatch):
    for name in (PREFIX, f"{PREFIX}.sources", f"{PREFIX}.jobs"):
        _module(name, __path__=[])
    settings = SimpleNamespace(
        inat_base_url="https://inat.fixture.invalid/v1", inat_api_token="",
        inat_domain_mode="fungi", inat_rate_limit=0, http_timeout=1,
    )
    _module(f"{PREFIX}.config", settings=settings)
    _module(f"{PREFIX}.checkpoint", CheckpointManager=object)
    _module(f"{PREFIX}.db", db_session=Mock(side_effect=AssertionError("no DB configured")))
    _module(f"{PREFIX}.taxon_canonicalizer", upsert_taxon=Mock(return_value="fixture-taxon"))
    _module(f"{PREFIX}.jobs.species_map_sync", upsert_species_map_rows=Mock())
    source = _load(f"{PREFIX}.sources.inat", "mindex_etl/sources/inat.py")
    sys.modules[f"{PREFIX}.sources"].inat = source
    job = _load(f"{PREFIX}.jobs.sync_inat_observations", "mindex_etl/jobs/sync_inat_observations.py")
    monkeypatch.setattr(source.time, "sleep", lambda _: None)
    yield SimpleNamespace(source=source, job=job)
    for name in list(sys.modules):
        if name == PREFIX or name.startswith(PREFIX + "."):
            sys.modules.pop(name)


class FixtureClient:
    def __init__(self, records=(), total=None):
        self.records = list(records)
        self.total = total
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, *, params, **kwargs):
        self.calls.append(deepcopy(params))
        page, count = params["page"], params["per_page"]
        body = {"results": self.records[(page - 1) * count:page * count]}
        if self.total is not None:
            body["total_results"] = self.total
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


@pytest.mark.parametrize("count,per_page,total,max_pages,expected", [
    (4, 2, 4, None, 4), (5, 2, 5, None, 5), (2, 1, 2, None, 2),
    (1, 1, 1, None, 1), (0, 2, 0, None, 0), (4, 2, None, None, 4),
    (4, 2, 4, 1, 2), (201, 300, 201, None, 201),
])
@pytest.mark.parametrize("mode,root_id", [("fungi", 47170), ("all", 1)])
def test_taxa_pagination_preserves_all_fixture_ids(contract, count, per_page, total, max_pages, expected, mode, root_id):
    records = [{"id": n, "name": f"Fixture taxon {n}", "rank": "species"} for n in range(1, count + 1)]
    client = FixtureClient(records, total)
    rows = list(contract.source.iter_inat_taxa(
        client=client, per_page=per_page, max_pages=max_pages,
        domain_mode=mode, delay_seconds=0, save_locally=False,
    ))
    assert [row[2] for row in rows] == [str(n) for n in range(1, expected + 1)]
    assert all(row[1] == "inat" for row in rows)
    assert all(call["taxon_id"] == root_id for call in client.calls)
    assert all(call["per_page"] == min(per_page, 200) for call in client.calls)
    assert [call["page"] for call in client.calls] == list(range(1, len(client.calls) + 1))
    if max_pages:
        assert len(client.calls) <= max_pages


class RecordingConnection:
    """Records emitted SQL and parameters; does not implement a database engine."""

    def __init__(self, exists=False):
        self.exists = exists
        self.calls = []
        self.sql = ""

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        assert sql.count("%s") == len(params), "SQL placeholder/binding mismatch"
        self.sql = " ".join(sql.split())
        self.calls.append((self.sql, deepcopy(tuple(params))))

    def fetchone(self):
        assert self.sql.startswith("SELECT 1 FROM obs.observation")
        return {"exists": 1} if self.exists else None

    def fetchall(self):
        assert self.sql.startswith("SELECT source_id FROM obs.observation")
        return [{"source_id": "fixture-observation-1"}]


def raw_observation(lat, lng):
    return {
        "id": "fixture-observation-1", "observed_on": "2026-09-28T12:00:00Z",
        "geojson": {"coordinates": [lng, lat]}, "user": {"login": "fixture-observer"},
        "taxon": {"id": 101, "name": "Fixture fungus", "rank": "species", "iconic_taxon_name": "Fungi"},
        "photos": [{"url": "https://media.fixture.invalid/square.jpg", "attribution": "Fixture author", "license_code": "fixture-license"}],
        "description": "Explicit deterministic test fixture", "uri": "https://inat.fixture.invalid/observations/fixture-1",
        "quality_grade": "research", "positional_accuracy": 5,
    }


def configure_job(contract, monkeypatch, connection, mapped):
    @contextmanager
    def fake_session():
        yield connection

    monkeypatch.setattr(contract.job, "db_session", fake_session)
    monkeypatch.setattr(contract.job, "iter_observations", lambda **_: iter([mapped]))


def observation_write(connection):
    return next((sql, params) for sql, params in connection.calls
                if sql.startswith(("UPDATE obs.observation", "INSERT INTO obs.observation")))


def binding_for(sql, params, expression):
    position = sql.index(expression)
    return params[sql[:position].count("%s")]


@pytest.mark.parametrize("lat,lng", [(0.0, 12.5), (12.5, 0.0), (0.0, 0.0), (45.0, -120.0), (None, 12.5), (12.5, None)])
@pytest.mark.parametrize("exists", [False, True])
def test_observation_coordinate_presence_and_provenance(contract, monkeypatch, lat, lng, exists):
    mapped = contract.job._map_observation(raw_observation(lat, lng))
    original = deepcopy(mapped)
    connection = RecordingConnection(exists)
    configure_job(contract, monkeypatch, connection, mapped)
    assert contract.job.sync_inat_observations(backfill_records=0) == 1
    sql, params = observation_write(connection)
    if lat is not None and lng is not None:
        assert "ST_MakePoint(%s, %s)" in sql
        position = sql.index("ST_MakePoint(")
        offset = sql[:position].count("%s")
        assert params[offset:offset + 2] == (lng, lat)
    else:
        assert "ST_MakePoint" not in sql
    assert mapped == original
    assert "fixture-observation-1" in params
    media = next(json.loads(value) for value in params if isinstance(value, str) and value.startswith("[{"))
    assert media[0]["license_code"] == "fixture-license"
    assert media[0]["attribution"] == "Fixture author"
    contract.job.upsert_species_map_rows.assert_called_once_with(connection, mapped, core_taxon_id="fixture-taxon")


@pytest.mark.parametrize("corrected_time", ["2026-09-28T12:00:00Z", None])
def test_existing_observation_refresh_binds_time_and_preserves_absent_correction(contract, monkeypatch, corrected_time):
    mapped = contract.job._map_observation(raw_observation(45.0, -120.0))
    mapped["observed_at"] = corrected_time
    connection = RecordingConnection(exists=True)
    configure_job(contract, monkeypatch, connection, mapped)
    contract.job.sync_inat_observations(backfill_records=0)
    sql, params = observation_write(connection)
    assert re.search(r"observed_at\s*=\s*COALESCE\(%s::timestamptz,\s*observed_at\)", sql)
    assert binding_for(sql, params, "COALESCE(%s::timestamptz, observed_at)") == corrected_time
    assert params[-2:] == ("inat", "fixture-observation-1")


@pytest.mark.parametrize("lat,lng", [(0.0, 12.5), (12.5, 0.0), (0.0, 0.0), (45.0, -120.0), (None, 12.5), (12.5, None)])
def test_metadata_backfill_preserves_zero_coordinates(contract, monkeypatch, lat, lng):
    connection = RecordingConnection()
    monkeypatch.setattr(contract.job.httpx, "Client", FixtureClient)
    monkeypatch.setattr(contract.job, "_fetch_observations_by_ids", lambda *_: {"results": [raw_observation(lat, lng)]})
    assert contract.job.backfill_missing_inat_observation_metadata(connection, delay_seconds=0) == 1
    sql, params = observation_write(connection)
    if lat is not None and lng is not None:
        assert "ST_MakePoint(%s, %s)" in sql
        offset = sql[:sql.index("ST_MakePoint(")].count("%s")
        assert params[offset:offset + 2] == (lng, lat)
    else:
        assert "ST_MakePoint" not in sql
    assert params[-1] == "fixture-observation-1"
