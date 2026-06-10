from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_sine_model_artifact_inference.py"
spec = importlib.util.spec_from_file_location("smoke_sine_model_artifact_inference", SCRIPT_PATH)
assert spec and spec.loader
smoke_sine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke_sine
spec.loader.exec_module(smoke_sine)


def test_mark_model_loaded_sql_is_checksum_guarded() -> None:
    sql = smoke_sine.mark_model_loaded_sql(
        {
            "model_id": "sine-esc50-cnn-p0-v1",
            "artifact_sha256": "artifact123",
            "label_map_sha256": "labels456",
        },
        {
            "status": "model_output_ready",
            "model_id": "sine-esc50-cnn-p0-v1",
            "artifact_sha256": "artifact123",
            "label_map_sha256": "labels456",
            "top_label": "thunderstorm",
            "confidence": 0.92,
            "ood_status": "in_domain_candidate",
            "embedding_sha256": "embed789",
            "feature_sha256": "feature000",
        },
    )

    assert "UPDATE sine.model_artifact" in sql
    assert "loaded = TRUE" in sql
    assert "status = 'model_ready'" in sql
    assert "WHERE model_id = 'sine-esc50-cnn-p0-v1'" in sql
    assert "artifact_sha256 = 'artifact123'" in sql
    assert "label_map_sha256 = 'labels456'" in sql
    assert "runtime_smoke" in sql


def test_safe_inference_payload_omits_raw_embedding() -> None:
    safe = smoke_sine._safe_inference_payload({"embedding": [1.0, 2.0, 3.0], "top_label": "rain"})

    assert safe["embedding"] == "<3 floats omitted>"
    assert safe["top_label"] == "rain"


def test_smoke_package_inference_refuses_to_pass_ood(monkeypatch, tmp_path: Path) -> None:
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    (package_root / "model_registry_row.json").write_text(
        json.dumps({"model_id": "sine-esc50-cnn-p0-v1", "input_sample_rate_hz": 16000}),
        encoding="utf-8",
    )
    wav_path = tmp_path / "clip.wav"
    wav_path.write_bytes(b"placeholder")

    class Verifier:
        @staticmethod
        def verify_package(package_root: Path, expected_model_id: str | None = None) -> dict[str, object]:
            return {"status": "verified", "package_root": str(package_root), "model_id": expected_model_id}

    monkeypatch.setattr(smoke_sine, "_load_verifier_module", lambda: Verifier)
    monkeypatch.setattr(smoke_sine, "load_mono", lambda path, target_sr=16000: ([0.0, 0.1], target_sr))
    monkeypatch.setattr(
        smoke_sine,
        "run_registered_model_inference",
        lambda samples, sample_rate, model_row, top_k=5: {
            "ok": True,
            "status": "model_output_ready",
            "model_id": "sine-esc50-cnn-p0-v1",
            "top_label": "uav_rotor",
            "confidence": 0.31,
            "ood_status": "out_of_domain_candidate",
            "artifact_sha256": "artifact123",
            "label_map_sha256": "labels456",
        },
    )

    report = smoke_sine.smoke_package_inference(
        package_root,
        wav_path,
        expected_model_id="sine-esc50-cnn-p0-v1",
    )

    assert report["status"] == "runtime_smoke_ood_review"
    assert report["ok"] is False
    assert report["loaded_sql"] is None


def test_smoke_package_inference_writes_loaded_sql_after_real_inference(monkeypatch, tmp_path: Path) -> None:
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    (package_root / "model_registry_row.json").write_text(
        json.dumps(
            {
                "model_id": "sine-esc50-cnn-p0-v1",
                "input_sample_rate_hz": 16000,
                "artifact_sha256": "artifact123",
                "label_map_sha256": "labels456",
            }
        ),
        encoding="utf-8",
    )
    wav_path = tmp_path / "clip.wav"
    wav_path.write_bytes(b"placeholder")

    class Verifier:
        @staticmethod
        def verify_package(package_root: Path, expected_model_id: str | None = None) -> dict[str, object]:
            return {"status": "verified", "package_root": str(package_root), "model_id": expected_model_id}

    monkeypatch.setattr(smoke_sine, "_load_verifier_module", lambda: Verifier)
    monkeypatch.setattr(smoke_sine, "load_mono", lambda path, target_sr=16000: ([0.0, 0.1], target_sr))
    monkeypatch.setattr(
        smoke_sine,
        "run_registered_model_inference",
        lambda samples, sample_rate, model_row, top_k=5: {
            "ok": True,
            "status": "model_output_ready",
            "model_id": "sine-esc50-cnn-p0-v1",
            "top_label": "rain",
            "confidence": 0.91,
            "ood_status": "in_domain_candidate",
            "artifact_sha256": "artifact123",
            "label_map_sha256": "labels456",
            "embedding": [0.1, 0.2],
        },
    )

    report = smoke_sine.smoke_package_inference(package_root, wav_path)

    assert report["status"] == "runtime_smoke_passed"
    assert report["ok"] is True
    assert report["loaded_sql"]
    assert report["inference"]["embedding"] == "<2 floats omitted>"
