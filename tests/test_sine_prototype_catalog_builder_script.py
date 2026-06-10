from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_sine_prototype_catalog.py"
spec = importlib.util.spec_from_file_location("build_sine_prototype_catalog", SCRIPT_PATH)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


def test_prototype_id_for_label_is_stable() -> None:
    assert builder.prototype_id_for_label("sine-esc50-cnn-p0-v1", "Lightning / Thunder") == (
        "sine-esc50-cnn-p0-v1--lightning-thunder"
    )


def test_prototype_insert_sql_contains_vector_metadata() -> None:
    sql = builder.prototype_insert_sql(
        [
            {
                "prototype_id": "sine-model--rain",
                "label": "rain",
                "category": "rain",
                "source": "ESC-50",
                "source_uri": "/mnt/nas/mindex/Library/acoustic/esc50",
                "license": "cc-by",
                "model_id": "sine-model",
                "embedding_dim": 2,
                "vector": [0.25, 0.75],
                "vector_sha256": "abc123",
                "prototype_sha256": "abc123",
                "example_count": 2,
                "metadata": {"semantic_fallback_used": False},
            }
        ]
    )

    assert "INSERT INTO sine.prototype" in sql
    assert "ON CONFLICT (prototype_id) DO UPDATE" in sql
    assert "sine-model--rain" in sql
    assert "prototype_vector" in sql
    assert "centroid" in sql
    assert "abc123" in sql


def test_build_prototypes_averages_embeddings(monkeypatch, tmp_path: Path) -> None:
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    (package_root / "model_registry_row.json").write_text(
        json.dumps(
            {
                "model_id": "sine-model",
                "input_sample_rate_hz": 16000,
                "artifact_sha256": "artifact123",
                "label_map_sha256": "labels456",
            }
        ),
        encoding="utf-8",
    )
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    first = audio_root / "a.wav"
    second = audio_root / "b.wav"
    first.write_bytes(b"placeholder")
    second.write_bytes(b"placeholder")
    metadata_csv = tmp_path / "esc50.csv"
    metadata_csv.write_text("filename,category\n" "a.wav,rain\n" "b.wav,rain\n", encoding="utf-8")

    class Verifier:
        @staticmethod
        def verify_package(package_root: Path, expected_model_id: str | None = None) -> dict[str, object]:
            return {"status": "verified", "package_root": str(package_root), "model_id": expected_model_id}

    calls = iter([[1.0, 0.0], [0.0, 1.0]])
    monkeypatch.setattr(builder, "_load_verifier_module", lambda: Verifier)
    monkeypatch.setattr(builder, "load_mono", lambda path, target_sr=16000: ([0.0, 0.1], target_sr))
    monkeypatch.setattr(
        builder,
        "run_registered_model_inference",
        lambda samples, sample_rate, model_row, top_k=5: {
            "ok": True,
            "status": "model_output_ready",
            "ood_status": "in_domain_candidate",
            "embedding": next(calls),
        },
    )

    report = builder.build_prototypes(
        package_root,
        audio_root,
        metadata_csv=metadata_csv,
        expected_model_id="sine-model",
        min_examples_per_label=2,
    )

    assert report["status"] == "prototype_catalog_ready"
    assert report["prototype_count"] == 1
    prototype = report["prototypes"][0]
    assert prototype["label"] == "rain"
    assert prototype["vector"] == [0.5, 0.5]
    assert prototype["example_count"] == 2
    assert prototype["metadata"]["semantic_fallback_used"] is False


def test_build_prototypes_refuses_ood_embeddings(monkeypatch, tmp_path: Path) -> None:
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    (package_root / "model_registry_row.json").write_text(
        json.dumps({"model_id": "sine-model", "input_sample_rate_hz": 16000}),
        encoding="utf-8",
    )
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    wav_path = audio_root / "a.wav"
    wav_path.write_bytes(b"placeholder")
    metadata_csv = tmp_path / "esc50.csv"
    metadata_csv.write_text("filename,category\n" "a.wav,rain\n", encoding="utf-8")

    class Verifier:
        @staticmethod
        def verify_package(package_root: Path, expected_model_id: str | None = None) -> dict[str, object]:
            return {"status": "verified", "package_root": str(package_root), "model_id": expected_model_id}

    monkeypatch.setattr(builder, "_load_verifier_module", lambda: Verifier)
    monkeypatch.setattr(builder, "load_mono", lambda path, target_sr=16000: ([0.0, 0.1], target_sr))
    monkeypatch.setattr(
        builder,
        "run_registered_model_inference",
        lambda samples, sample_rate, model_row, top_k=5: {
            "ok": True,
            "status": "model_output_ready",
            "ood_status": "out_of_domain_candidate",
            "embedding": [1.0, 0.0],
        },
    )

    report = builder.build_prototypes(package_root, audio_root, metadata_csv=metadata_csv)

    assert report["status"] == "prototype_catalog_empty"
    assert report["ok"] is False
    assert report["skipped"][0]["reason"] == "out_of_domain_candidate"
