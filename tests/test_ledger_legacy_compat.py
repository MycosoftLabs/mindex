"""Legacy ledger compatibility switch: on by default, 503 before any DB use when off."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from mindex_api.auth import require_internal_token
from mindex_api.dependencies import get_db_session, require_api_key
from mindex_api.ledger.legacy_gate import LEGACY_TENANT_MIGRATION_REQUIRED, legacy_routes_enabled
from mindex_api.main import create_app

LEGACY_REQUESTS = [
    ("GET", "/api/mindex/ledger/anchors"),
    ("GET", "/api/mindex/ledger/dag/epoch/current"),
    ("POST", "/api/mindex/ledger/anchor"),
    ("POST", "/api/mindex/ledger/anchor/records"),
    ("POST", "/api/mindex/ledger/mark-ip/00000000-0000-4000-8000-000000000001"),
    ("GET", "/api/mindex/integrity/summary"),
    ("GET", "/api/mindex/integrity/records/recent"),
    ("GET", "/api/mindex/verify/00000000-0000-4000-8000-000000000001"),
    ("GET", "/api/mindex/ip/assets"),
]


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class ScriptedSession:
    def __init__(self, responses):
        self.responses = list(responses)

    async def execute(self, *_args, **_kwargs):
        return self.responses.pop(0)

    async def rollback(self):
        return None


class UntouchableSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("retired legacy route reached the database")

    async def rollback(self):
        return None


def _app_with_session(session):
    app = create_app()
    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[require_internal_token] = lambda: "internal-test-token"

    async def override():
        yield session

    app.dependency_overrides[get_db_session] = override
    return app


@pytest.mark.parametrize("raw", [None, "", "true", "1", "yes", "anything-else"])
def test_legacy_routes_default_on(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("LEDGER_LEGACY_ROUTES_ENABLED", raising=False)
    else:
        monkeypatch.setenv("LEDGER_LEGACY_ROUTES_ENABLED", raw)
    assert legacy_routes_enabled() is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", " off "])
def test_legacy_routes_disabled_values(monkeypatch, raw):
    monkeypatch.setenv("LEDGER_LEGACY_ROUTES_ENABLED", raw)
    assert legacy_routes_enabled() is False


def test_enabled_legacy_ip_asset_list_keeps_original_contract(monkeypatch):
    monkeypatch.delenv("LEDGER_LEGACY_ROUTES_ENABLED", raising=False)
    asset_id = str(uuid4())
    row = {
        "id": asset_id, "name": "MSA", "description": None, "taxon_id": None,
        "created_by": None, "content_hash": None, "content_uri": None, "metadata": {},
        "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T00:00:00Z",
        "hypergraph_anchors": [], "bitcoin_ordinals": [], "solana_bindings": [],
    }
    client = TestClient(_app_with_session(ScriptedSession([Rows([row]), Scalar(1)])))
    response = client.get("/api/mindex/ip/assets")
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == asset_id


@pytest.mark.parametrize(("method", "path"), LEGACY_REQUESTS)
def test_disabled_legacy_routes_fail_closed_before_database(monkeypatch, method, path):
    monkeypatch.setenv("LEDGER_LEGACY_ROUTES_ENABLED", "false")
    client = TestClient(_app_with_session(UntouchableSession()))
    response = client.request(method, path, json={})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == LEGACY_TENANT_MIGRATION_REQUIRED


def test_disabling_legacy_routes_does_not_touch_provenance_api(monkeypatch):
    monkeypatch.setenv("LEDGER_LEGACY_ROUTES_ENABLED", "false")
    client = TestClient(_app_with_session(UntouchableSession()))
    response = client.get("/api/mindex/ledger/provenance/v1/records")
    assert LEGACY_TENANT_MIGRATION_REQUIRED not in response.text
