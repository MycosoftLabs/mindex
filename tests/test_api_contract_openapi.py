from __future__ import annotations

from fastapi.testclient import TestClient

from mindex_api.main import create_app


def test_openapi_is_namespaced_under_api_prefix() -> None:
    app = create_app()
    client = TestClient(app)

    # OpenAPI spec is served at the root-level /openapi.json (FastAPI default)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200

    spec = resp.json()
    paths = spec["paths"]

    assert paths, "OpenAPI spec should contain paths"

    # All paths should be namespaced (no bare /taxa, /health, etc.)
    for path in paths:
        assert path.startswith("/api/") or path == "/health", (
            f"Un-namespaced path found: {path}"
        )

    # Key endpoints must exist under their respective prefixes
    assert "/api/mindex/health" in paths
    assert "/api/mindex/taxa" in paths


def test_openapi_contract_includes_stable_dto_shapes() -> None:
    app = create_app()
    client = TestClient(app)

    spec = client.get("/openapi.json").json()
    schemas = spec.get("components", {}).get("schemas", {})

    # Health contract
    assert "HealthResponse" in schemas
    health_props = schemas["HealthResponse"]["properties"]
    assert "status" in health_props
    assert "db" in health_props
    assert "timestamp" in health_props

