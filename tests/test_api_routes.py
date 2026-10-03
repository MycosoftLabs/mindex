from __future__ import annotations

from typing import Any, List
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from mindex_api.main import create_app
from mindex_api.dependencies import get_db_session, require_api_key
from mindex_api.auth import require_internal_token


class FakeMappingsResult:
    def __init__(self, rows: List[dict]):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class FakeScalarResult:
    def __init__(self, value: Any):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, responses: List[Any]):
        self._responses = responses
        self.rollback_calls = 0

    async def execute(self, *_args, **_kwargs):
        if not self._responses:
            raise AssertionError("No more fake responses defined")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def rollback(self):
        self.rollback_calls += 1


@pytest.fixture
def app():
    app = create_app()
    yield app
    app.dependency_overrides.clear()


def _db_override(responses: List[Any]):
    session = FakeSession(responses.copy())

    async def override():
        yield session

    override.session = session
    return override


def _disable_api_key(app):
    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[require_internal_token] = lambda: "internal-test-token"


@pytest.mark.parametrize("index_error", [False, True], ids=["index-missing", "index-error"])
def test_list_taxa_route(app, index_error):
    _disable_api_key(app)
    taxon_id = str(uuid4())
    app.dependency_overrides[get_db_session] = _db_override(
        [
            FakeMappingsResult([
                {
                    "id": taxon_id,
                    "canonical_name": "Agaricus testus",
                    "rank": "species",
                    "common_name": "Test cap",
                    "authority": None,
                    "description": None,
                    "source": "seed",
                    "metadata": {},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ]),
            FakeScalarResult(1),
            RuntimeError("private-fixture-index-detail") if index_error else FakeScalarResult(None),
        ]
    )
    client = TestClient(app)
    resp = client.get("/api/mindex/taxa")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["data"][0]["canonical_name"] == "Agaricus testus"
    assert payload["pagination"]["total"] == 1
    assert payload["data"][0]["fungip"] is None
    _assert_optional_index_response(app, resp, index_error)


def _assert_optional_index_response(app, response, index_error):
    index = response.json()["fungip_index"]
    assert index["status"] == ("error" if index_error else "unavailable")
    assert index["reason"] == ("query_failed" if index_error else "source_table_missing")
    assert "private-fixture-index-detail" not in response.text
    session = app.dependency_overrides[get_db_session].session
    assert session.rollback_calls == int(index_error)
    assert session._responses == []


@pytest.mark.parametrize("index_error", [False, True], ids=["index-missing", "index-error"])
def test_get_taxon_route(app, monkeypatch, index_error):
    queued = []
    monkeypatch.setattr("mindex_api.routers.taxon._queue_incomplete_taxon", lambda *args: queued.append(args))
    _disable_api_key(app)
    taxon_id = str(uuid4())
    app.dependency_overrides[get_db_session] = _db_override(
        [
            FakeMappingsResult(
                [
                    {
                        "id": taxon_id,
                        "canonical_name": "Agaricus testus",
                        "rank": "species",
                        "common_name": None,
                        "authority": None,
                        "description": None,
                        "source": "seed",
                        "metadata": {},
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                        "traits": [],
                    }
                ]
            ),
            RuntimeError("private-fixture-index-detail") if index_error else FakeScalarResult(None),
        ]
    )
    client = TestClient(app)
    resp = client.get(f"/api/mindex/taxa/{taxon_id}")
    assert resp.status_code == 200
    assert resp.json()["canonical_name"] == "Agaricus testus"
    assert resp.json()["fungip"] is None
    _assert_optional_index_response(app, resp, index_error)
    assert len(queued) == 1 and queued[0][0] == taxon_id


def test_telemetry_latest_route(app):
    _disable_api_key(app)
    sample_id = str(uuid4())
    app.dependency_overrides[get_db_session] = _db_override(
        [
            FakeMappingsResult(
                [
                    {
                        "device_id": str(uuid4()),
                        "device_name": "Device 1",
                        "device_slug": "device-1",
                        "stream_id": str(uuid4()),
                        "stream_key": "temperature",
                        "stream_unit": "C",
                        "sample_id": sample_id,
                        "recorded_at": "2024-01-01T00:00:00Z",
                        "value_numeric": 21.5,
                        "value_text": None,
                        "value_json": None,
                        "value_unit": "C",
                        "sample_metadata": {},
                        "sample_location_geojson": None,
                        "device_location_geojson": None,
                    }
                ]
            )
        ]
    )
    client = TestClient(app)
    resp = client.get("/api/mindex/telemetry/devices/latest")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["sample_id"] == sample_id


def test_ip_assets_list_route(app):
    _disable_api_key(app)
    asset_id = str(uuid4())
    responses = [
        FakeMappingsResult(
            [
                {
                    "id": asset_id,
                    "name": "MSA",
                    "description": None,
                    "taxon_id": None,
                    "created_by": None,
                    "content_hash": None,
                    "content_uri": None,
                    "metadata": {},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "hypergraph_anchors": [],
                    "bitcoin_ordinals": [],
                    "solana_bindings": [],
                }
            ]
        ),
        FakeScalarResult(1),
    ]
    app.dependency_overrides[get_db_session] = _db_override(responses)
    client = TestClient(app)
    resp = client.get("/api/mindex/ip/assets")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == asset_id
