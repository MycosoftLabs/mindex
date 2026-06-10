from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "train_sine_esc50_p0.py"
spec = importlib.util.spec_from_file_location("train_sine_esc50_p0", SCRIPT_PATH)
assert spec and spec.loader
train_sine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = train_sine
spec.loader.exec_module(train_sine)


def test_discovers_esc50_labels_from_metadata_csv(tmp_path: Path) -> None:
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    wav_path = audio_root / "1-100038-A-14.wav"
    wav_path.write_bytes(b"not decoded during discovery")
    meta_path = tmp_path / "esc50.csv"
    with meta_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["filename", "fold", "target", "category"])
        writer.writeheader()
        writer.writerow({"filename": wav_path.name, "fold": "1", "target": "14", "category": "chirping_birds"})

    records = train_sine.discover_audio_records(audio_root, metadata_csv=meta_path)

    assert len(records) == 1
    assert records[0].path == wav_path
    assert records[0].label == "chirping_birds"
    assert records[0].fold == 1
    assert records[0].target_id == 14


def test_discovery_requires_real_labels_unless_target_id_fallback_enabled(tmp_path: Path) -> None:
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    wav_path = audio_root / "1-100038-A-14.wav"
    wav_path.write_bytes(b"not decoded during discovery")

    try:
        train_sine.discover_audio_records(audio_root)
    except RuntimeError as exc:
        assert "no labeled WAV files" in str(exc)
    else:
        raise AssertionError("expected discovery to reject unlabeled audio")

    records = train_sine.discover_audio_records(audio_root, allow_target_id_labels=True)

    assert records[0].label == "esc50_target_14"
    assert records[0].target_id == 14


def test_registry_insert_sql_contains_checksummed_artifact_fields() -> None:
    sql = train_sine.registry_insert_sql(
        {
            "model_id": "sine-esc50-cnn-p0-v1",
            "model_name": "SINE ESC-50 CNN P0",
            "model_version": "p0",
            "domain": "acoustic",
            "target_domains": ["air"],
            "class_families": ["terrestrial_bioacoustics", "unknown_pattern"],
            "framework": "torchscript",
            "runtime": "torch",
            "artifact_uri": "/mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/model.torchscript.pt",
            "artifact_sha256": "abc123",
            "label_map_uri": "/mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/labels.json",
            "label_map_sha256": "def456",
            "training_dataset": "ESC-50",
            "metrics_uri": "/mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/metrics.json",
            "confusion_matrix_uri": "/mnt/nas/mindex/models/acoustic/sine-esc50-cnn-p0-v1/confusion_matrix.json",
            "input_sample_rate_hz": 16000,
            "window_sec": 5.0,
            "label_count": 50,
            "embedding_dim": 128,
            "device": "cpu",
            "status": "trained",
            "loaded": False,
            "feature_params": {"n_fft": 1024, "hop_length": 256, "n_mels": 64, "max_frames": 256},
        }
    )

    assert "INSERT INTO sine.model_artifact" in sql
    assert "ON CONFLICT (model_id) DO UPDATE" in sql
    assert "artifact_sha256" in sql
    assert "label_map_sha256" in sql
    assert "feature_params" in sql
    assert "::jsonb" in sql
