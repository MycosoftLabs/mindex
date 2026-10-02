from __future__ import annotations

from unittest.mock import AsyncMock, create_autospec
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mindex_api.auth import CallerIdentity, require_worldview_key
from mindex_api.dependencies import get_db_session
from mindex_api.routers import unified_search as internal_search
from mindex_api.routers.worldview import search


@pytest.fixture
def search_client(monkeypatch):
    app = FastAPI()
    app.include_router(search.router, prefix="/api/worldview/v1")
    db = object()
    caller = CallerIdentity(uuid4(), uuid4(), "human", "pro")
    app.dependency_overrides[require_worldview_key] = lambda: caller
    app.dependency_overrides[get_db_session] = lambda: db
    service = create_autospec(internal_search.unified_search)
    monkeypatch.setattr(internal_search, "unified_search", service)

    async def wrap(**kwargs):
        return {"data": kwargs["data"], "meta": {"source_domains": kwargs["source_domains"]}}

    monkeypatch.setattr(search, "wrap_governed_response", AsyncMock(side_effect=wrap))
    return TestClient(app), service, db


@pytest.mark.parametrize("as_model", [False, True])
def test_search_adapts_domain_buckets_and_real_service_signature(search_client, as_model):
    client, service, db = search_client
    payload = {
        "query": "agaricus", "domains_searched": ["taxa"],
        "results": {"taxa": [{"id": "taxon-1"}], "devices": [{"id": "private"}]},
        "total_count": 2, "timing_ms": 0,
    }
    service.return_value = internal_search.UnifiedSearchResponse(**payload) if as_model else payload
    response = client.get("/api/worldview/v1/search", params={
        "q": "agaricus", "domains": "taxa,devices", "lat": 32, "lng": -117,
        "radius_km": 4, "limit": 2,
    })
    assert response.status_code == 200
    assert response.json()["data"] == [{"id": "taxon-1", "domain": "taxa"}]
    assert response.json()["meta"]["source_domains"] == ["taxa"]
    args = service.call_args.kwargs
    assert args["types"] == "taxa"
    assert (args["lat"], args["lng"], args["radius"], args["limit"]) == (32, -117, 4, 2)
    assert args["session"] is db
    assert all(args[name] is None for name in ("toxicity", "kingdom", "facility_type", "since", "until"))


@pytest.mark.parametrize("domains", ["devices", "telemetry,unknown", " , "])
def test_no_allowed_domains_does_not_expand_to_all(search_client, domains):
    client, service, _ = search_client
    response = client.get("/api/worldview/v1/search", params={"q": "agaricus", "domains": domains})
    assert response.status_code == 400
    service.assert_not_called()


def test_default_domains_and_total_limit_are_public_contract(search_client):
    client, service, _ = search_client
    service.return_value = {"results": {
        "taxa": [{"id": "one"}, {"id": "two"}],
        "species": [{"id": "three"}],
        "telemetry": [{"id": "private"}],
    }}
    response = client.get("/api/worldview/v1/search", params={"q": "agaricus", "limit": 2})
    assert response.status_code == 200
    assert len(response.json()["data"]) == 2
    assert set(service.call_args.kwargs["types"].split(",")) == set(search.WORLDVIEW_DOMAINS)
    assert service.call_args.kwargs["radius"] == 100


def test_empty_domain_buckets_remain_empty(search_client):
    client, service, _ = search_client
    service.return_value = {"results": {"taxa": []}}
    response = client.get("/api/worldview/v1/search", params={"q": "agaricus", "domains": "taxa"})
    assert response.status_code == 200
    assert response.json()["data"] == []
