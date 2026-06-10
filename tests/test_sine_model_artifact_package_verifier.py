from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from mindex_api.services.sine_acoustic.model_runtime import sha256_file


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_sine_model_artifact_package.py"
spec = importlib.util.spec_from_file_location("verify_sine_model_artifact_package", SCRIPT_PATH)
assert spec and spec.loader
verify_sine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verify_sine
spec.loader.exec_module(verify_sine)


def _write_valid_package(package_root: Path, *, loaded: bool = False) -> dict[str, str]:
    package_root.mkdir()
    model_path = package_root / "model.torchscript.pt"
    labels_path = package_root / "labels.json"
    metrics_path = package_root / "metrics.json"
    confusion_path = package_root / "confusion_matrix.json"
    manifest_path = package_root / "training_manifest.json"
    registry_path = package_root / "model_registry_row.json"
    sql_path = package_root / "register_model_artifact.sql"

    model_path.write_bytes(b"fake torchscript bytes for verifier only")
    labels = {"labels": ["rain", "thunder"], "source": "ESC-50"}
    labels_path.write_text(json.dumps(labels, sort_keys=True), encoding="utf-8")
    metrics = {
        "model_id": "sine-esc50-cnn-p0-v1",
        "train_records": 8,
        "validation_records": 2,
        "label_count": 2,
        "validation_accuracy": 0.5,
        "validation_total": 2,
        "validation_correct": 1,
    }
    metrics_path.write_text(json.dumps(metrics, sort_keys=True), encoding="utf-8")
    confusion_path.write_text(
        json.dumps({"labels": ["rain", "thunder"], "matrix": [[1, 0], [0, 1]]}, sort_keys=True),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "model_id": "sine-esc50-cnn-p0-v1",
                "train_records": 8,
                "validation_records": 2,
                "labels": ["rain", "thunder"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    registry = {
        "model_id": "sine-esc50-cnn-p0-v1",
        "model_name": "SINE ESC-50 CNN P0",
        "model_version": "p0",
        "domain": "acoustic",
        "target_domains": ["air"],
        "class_families": ["weather_lightning", "unknown_pattern"],
        "framework": "torchscript",
        "runtime": "torch",
        "artifact_uri": str(model_path),
        "artifact_sha256": sha256_file(model_path),
        "label_map_uri": str(labels_path),
        "label_map_sha256": sha256_file(labels_path),
        "training_dataset": "ESC-50",
        "metrics_uri": str(metrics_path),
        "confusion_matrix_uri": str(confusion_path),
        "input_sample_rate_hz": 16000,
        "window_sec": 5.0,
        "label_count": 2,
        "embedding_dim": 128,
        "device": "cpu",
        "status": "trained",
        "loaded": loaded,
        "feature_params": {"n_fft": 1024, "hop_length": 256, "n_mels": 64, "max_frames": 256, "window_sec": 5.0},
    }
    registry_path.write_text(json.dumps(registry, sort_keys=True), encoding="utf-8")
    sql_path.write_text(
        "INSERT INTO sine.model_artifact (model_id, artifact_sha256, label_map_sha256) "
        f"VALUES ('sine-esc50-cnn-p0-v1', '{registry['artifact_sha256']}', '{registry['label_map_sha256']}') "
        "ON CONFLICT (model_id) DO UPDATE SET artifact_sha256 = EXCLUDED.artifact_sha256;",
        encoding="utf-8",
    )
    return {"artifact_sha256": registry["artifact_sha256"], "label_map_sha256": registry["label_map_sha256"]}


def test_verifies_valid_package(tmp_path: Path) -> None:
    package_root = tmp_path / "sine-esc50-cnn-p0-v1"
    _write_valid_package(package_root)

    report = verify_sine.verify_package(package_root, expected_model_id="sine-esc50-cnn-p0-v1")

    assert report["status"] == "verified"
    assert report["model_id"] == "sine-esc50-cnn-p0-v1"
    assert report["label_count"] == 2
    assert report["failures"] == []


def test_fails_on_checksum_mismatch(tmp_path: Path) -> None:
    package_root = tmp_path / "sine-esc50-cnn-p0-v1"
    _write_valid_package(package_root)
    registry_path = package_root / "model_registry_row.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["artifact_sha256"] = "bad"
    registry_path.write_text(json.dumps(registry, sort_keys=True), encoding="utf-8")

    report = verify_sine.verify_package(package_root)

    assert report["status"] == "failed"
    assert any(failure["name"] == "artifact.sha256" for failure in report["failures"])


def test_fails_if_package_claims_loaded_before_runtime_probe(tmp_path: Path) -> None:
    package_root = tmp_path / "sine-esc50-cnn-p0-v1"
    _write_valid_package(package_root, loaded=True)

    report = verify_sine.verify_package(package_root)

    assert report["status"] == "failed"
    assert any(failure["name"] == "loaded.false" for failure in report["failures"])


def test_fails_on_malformed_confusion_matrix(tmp_path: Path) -> None:
    package_root = tmp_path / "sine-esc50-cnn-p0-v1"
    _write_valid_package(package_root)
    confusion_path = package_root / "confusion_matrix.json"
    confusion_path.write_text(
        json.dumps({"labels": ["rain", "thunder"], "matrix": [[1, 0, 0], [0, 1, 0]]}, sort_keys=True),
        encoding="utf-8",
    )

    report = verify_sine.verify_package(package_root)

    assert report["status"] == "failed"
    assert any(failure["name"] == "confusion.matrix_square" for failure in report["failures"])


def test_fails_when_validation_totals_do_not_match(tmp_path: Path) -> None:
    package_root = tmp_path / "sine-esc50-cnn-p0-v1"
    _write_valid_package(package_root)
    metrics_path = package_root / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["validation_total"] = 9
    metrics_path.write_text(json.dumps(metrics, sort_keys=True), encoding="utf-8")

    report = verify_sine.verify_package(package_root)

    assert report["status"] == "failed"
    assert any(failure["name"] == "metrics.validation_total_matches" for failure in report["failures"])
