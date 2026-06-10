from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mindex_api.services.sine_acoustic import inference_runtime
from mindex_api.services.sine_acoustic.inference_runtime import run_registered_model_inference
from mindex_api.services.sine_acoustic.model_runtime import sha256_file
from mindex_api.services.sine_acoustic.prototype_search import vector_sha256


def _write_registry_files(tmp_path: Path) -> tuple[Path, Path]:
    artifact = tmp_path / "model.ts"
    artifact.write_bytes(b"not-a-real-model-but-checksummed")
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(["rain", "lightning_thunder", "uav_rotor"]), encoding="utf-8")
    return artifact, labels


def _model_record(artifact: Path, labels: Path, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "model_id": "sine-test-v1",
        "model_name": "SINE Test Model",
        "model_version": "0.0.1",
        "framework": "pytorch",
        "runtime": "torchscript",
        "artifact_uri": str(artifact),
        "artifact_sha256": sha256_file(artifact),
        "label_map_uri": str(labels),
        "label_map_sha256": sha256_file(labels),
        "input_sample_rate_hz": 16000,
        "window_sec": 1.0,
        "feature_params": {"n_fft": 512, "hop_length": 128, "n_mels": 32, "max_frames": 64},
    }
    record.update(overrides)
    return record


def _tone(sr: int = 16000, seconds: float = 0.5) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def test_run_registered_model_inference_returns_unavailable_when_runtime_missing(tmp_path: Path) -> None:
    artifact, labels = _write_registry_files(tmp_path)
    result = run_registered_model_inference(_tone(), 16000, _model_record(artifact, labels))

    assert result["ok"] is False
    assert result["status"] == "model_runtime_unavailable"
    assert result["model_outputs"] == []
    assert "torch" in result["detail"]


def test_run_registered_model_inference_rejects_artifact_checksum_mismatch(tmp_path: Path) -> None:
    artifact, labels = _write_registry_files(tmp_path)
    record = _model_record(artifact, labels, artifact_sha256="bad")
    result = run_registered_model_inference(_tone(), 16000, record)

    assert result["ok"] is False
    assert result["status"] == "model_artifact_checksum_mismatch"
    assert result["expected_artifact_sha256"] == "bad"


def test_run_registered_model_inference_rejects_sample_rate_mismatch(tmp_path: Path) -> None:
    artifact, labels = _write_registry_files(tmp_path)
    result = run_registered_model_inference(_tone(sr=8000), 8000, _model_record(artifact, labels))

    assert result["ok"] is False
    assert result["status"] == "sample_rate_mismatch"
    assert result["expected_sample_rate_hz"] == 16000


def test_run_registered_model_inference_maps_mocked_torchscript_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, labels = _write_registry_files(tmp_path)

    monkeypatch.setattr(inference_runtime, "runtime_backend_status", lambda: {"torch": True, "onnxruntime": False})
    monkeypatch.setattr(
        inference_runtime,
        "_run_torchscript",
        lambda artifact_path, tensor: np.asarray([[0.1, 2.5, -0.2]], dtype=np.float32),
    )

    result = run_registered_model_inference(_tone(), 16000, _model_record(artifact, labels), top_k=2)

    assert result["ok"] is True
    assert result["status"] == "model_output_ready"
    assert result["model_status"] == "model_ready"
    assert result["runtime"] == "torchscript"
    assert result["top_label"] == "lightning_thunder"
    assert len(result["labels"]) == 2
    assert result["ood_status"] == "in_domain_candidate"
    assert 0 <= result["ood_score"] <= 1
    assert result["confidence_margin"] > 0
    assert result["artifact_sha256"] == sha256_file(artifact)
    assert result["label_map_sha256"] == sha256_file(labels)
    assert result["feature_sha256"] == result["feature_metadata"]["feature_sha256"]
    assert result["tensor_shape"] == [1, 1, 32, 64]
    assert result["feature_metadata"]["semantic_free"] is True


def test_run_registered_model_inference_preserves_tuple_embedding_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, labels = _write_registry_files(tmp_path)
    embedding = np.asarray([[0.5, -0.25, 0.75, 1.0]], dtype=np.float32)

    monkeypatch.setattr(inference_runtime, "runtime_backend_status", lambda: {"torch": True, "onnxruntime": False})
    monkeypatch.setattr(
        inference_runtime,
        "_run_torchscript",
        lambda artifact_path, tensor: [
            np.asarray([[0.1, 2.5, -0.2]], dtype=np.float32),
            embedding,
        ],
    )

    result = run_registered_model_inference(_tone(), 16000, _model_record(artifact, labels), top_k=2)

    assert result["ok"] is True
    assert result["top_label"] == "lightning_thunder"
    assert result["embedding"] == [0.5, -0.25, 0.75, 1.0]
    assert result["embedding_dim"] == 4
    assert result["embedding_sha256"] == vector_sha256([0.5, -0.25, 0.75, 1.0])


def test_run_registered_model_inference_preserves_dict_embedding_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, labels = _write_registry_files(tmp_path)

    monkeypatch.setattr(inference_runtime, "runtime_backend_status", lambda: {"torch": True, "onnxruntime": False})
    monkeypatch.setattr(
        inference_runtime,
        "_run_torchscript",
        lambda artifact_path, tensor: {
            "logits": np.asarray([[0.1, 2.5, -0.2]], dtype=np.float32),
            "embedding": [1.0, 0.0, 0.0],
        },
    )

    result = run_registered_model_inference(_tone(), 16000, _model_record(artifact, labels), top_k=2)

    assert result["ok"] is True
    assert result["top_label"] == "lightning_thunder"
    assert result["embedding"] == [1.0, 0.0, 0.0]
    assert result["embedding_sha256"] == vector_sha256([1.0, 0.0, 0.0])


def test_run_registered_model_inference_marks_low_confidence_ood(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, labels = _write_registry_files(tmp_path)

    monkeypatch.setattr(inference_runtime, "runtime_backend_status", lambda: {"torch": True, "onnxruntime": False})
    monkeypatch.setattr(
        inference_runtime,
        "_run_torchscript",
        lambda artifact_path, tensor: np.asarray([[0.34, 0.33, 0.33]], dtype=np.float32),
    )

    result = run_registered_model_inference(
        _tone(),
        16000,
        _model_record(
            artifact,
            labels,
            feature_params={
                "n_fft": 512,
                "hop_length": 128,
                "n_mels": 32,
                "max_frames": 64,
                "min_confidence": 0.6,
                "ood_threshold": 0.8,
            },
        ),
    )

    assert result["ok"] is True
    assert result["ood_status"] == "low_confidence"
    assert result["ood_score"] >= 0.5
    assert result["confidence"] < 0.6
