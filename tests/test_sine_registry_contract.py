from __future__ import annotations

from mindex_api.routers.sine_acoustic import _registry_unavailable


def test_missing_model_registry_is_honest() -> None:
    payload = _registry_unavailable("models")
    assert payload["ok"] is False
    assert payload["status"] == "model_registry_unavailable"
    assert payload["model_status"] == "model_unavailable"
    assert payload["model_ready"] is False
    assert payload["models"] == []
    assert payload["loaded_models"] == []


def test_missing_prototype_catalog_is_honest() -> None:
    payload = _registry_unavailable("prototypes")
    assert payload["ok"] is False
    assert payload["status"] == "prototype_catalog_unavailable"
    assert payload["model_status"] == "model_unavailable"
    assert payload["prototype_ready"] is False
    assert payload["prototypes"] == []
    assert payload["prototype_catalog"] == []
