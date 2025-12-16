from __future__ import annotations

from fastapi.testclient import TestClient

from mindex_api.main import create_app


def test_openapi_is_namespaced_under_api_prefix() -> None:
    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/mindex/openapi.json")
    assert resp.status_code == 200

    spec = resp.json()
    paths = spec["paths"]

    assert paths, "OpenAPI spec should contain paths"
    assert all(path.startswith("/api/mindex/") for path in paths.keys())

    # Guardrails: no accidental un-namespaced routes.
    assert "/taxa" not in paths
    assert "/health" not in paths
    assert "/telemetry/devices/latest" not in paths

    # A few representative endpoints we promise externally.
    assert "/api/mindex/health" in paths
    assert "/api/mindex/taxa" in paths
    assert "/api/mindex/telemetry/devices/latest" in paths


def test_openapi_contract_includes_stable_dto_shapes() -> None:
    app = create_app()
    client = TestClient(app)

    spec = client.get("/api/mindex/openapi.json").json()
    schemas = spec["components"]["schemas"]

    # Taxa contract
    assert "TaxonListResponse" in schemas
    tlr_props = schemas["TaxonListResponse"]["properties"]
    assert "data" in tlr_props
    assert "pagination" in tlr_props

    # Health contract
    assert "HealthResponse" in schemas
    health_props = schemas["HealthResponse"]["properties"]
    assert set(["status", "db", "timestamp"]).issubset(set(health_props.keys()))

    # Telemetry contract
    assert "DeviceLatestSamplesResponse" in schemas
    dlsr_props = schemas["DeviceLatestSamplesResponse"]["properties"]
    assert "data" in dlsr_props
    assert "pagination" in dlsr_props

