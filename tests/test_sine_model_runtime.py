from __future__ import annotations

from pathlib import Path

from mindex_api.services.sine_acoustic.model_runtime import (
    artifact_path_from_uri,
    runtime_backend_status,
)


def test_artifact_path_from_uri_only_accepts_local_paths() -> None:
    assert artifact_path_from_uri("file:///mnt/nas/mindex/models/acoustic/model.onnx") == Path(
        "/mnt/nas/mindex/models/acoustic/model.onnx"
    )
    assert artifact_path_from_uri("/mnt/nas/mindex/models/acoustic/model.pt") == Path(
        "/mnt/nas/mindex/models/acoustic/model.pt"
    )
    assert artifact_path_from_uri("https://example.com/model.onnx") is None
    assert artifact_path_from_uri("s3://bucket/model.onnx") is None
    assert artifact_path_from_uri("") is None


def test_runtime_backend_status_is_boolean_map() -> None:
    status = runtime_backend_status()
    assert set(status) == {"torch", "onnxruntime"}
    assert isinstance(status["torch"], bool)
    assert isinstance(status["onnxruntime"], bool)
