#!/usr/bin/env python
"""Train and export the first SINE ESC-50 TorchScript artifact.

This script is intentionally artifact-only: it does not register fake model
rows, does not write analysis evidence, and does not invent labels. It trains
from real ESC-50 WAV files plus ESC-50 metadata, then writes a checksum-backed
artifact package that can be registered in `sine.model_artifact`.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from mindex_api.services.sine_acoustic.features import extract_sine_feature_tensor
from mindex_api.services.sine_acoustic.model_runtime import sha256_file


DEFAULT_MODEL_ID = "sine-esc50-cnn-p0-v1"
DEFAULT_MODEL_NAME = "SINE ESC-50 CNN P0"
DEFAULT_FEATURE_PARAMS = {
    "n_fft": 1024,
    "hop_length": 256,
    "n_mels": 64,
    "max_frames": 256,
    "window_sec": 5.0,
}


@dataclass(frozen=True)
class AudioRecord:
    path: Path
    label: str
    fold: int | None = None
    target_id: int | None = None


def _read_esc50_metadata(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            filename = str(row.get("filename") or "").strip()
            category = str(row.get("category") or "").strip()
            if not filename or not category:
                continue
            parsed = dict(row)
            for key in ("fold", "target"):
                try:
                    parsed[key] = int(str(row.get(key) or "").strip())
                except ValueError:
                    parsed[key] = None
            rows[filename] = parsed
    return rows


def _read_manifest_label(wav_path: Path) -> str | None:
    candidates = [
        wav_path.with_suffix(wav_path.suffix + ".manifest.json"),
        wav_path.with_suffix(".manifest.json"),
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("label", "category", "label_primary", "class_name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        catalog = payload.get("catalog")
        if isinstance(catalog, dict):
            for key in ("label", "category", "label_primary"):
                value = catalog.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _target_id_from_esc50_filename(path: Path) -> int | None:
    # ESC-50 filenames are shaped like `1-100038-A-14.wav`.
    try:
        return int(path.stem.rsplit("-", 1)[-1])
    except ValueError:
        return None


def discover_audio_records(
    audio_root: Path,
    *,
    metadata_csv: Path | None = None,
    max_files: int | None = None,
    allow_target_id_labels: bool = False,
) -> list[AudioRecord]:
    metadata = _read_esc50_metadata(metadata_csv)
    records: list[AudioRecord] = []
    for wav_path in sorted(audio_root.rglob("*.wav")):
        meta = metadata.get(wav_path.name)
        if meta:
            records.append(
                AudioRecord(
                    path=wav_path,
                    label=str(meta["category"]),
                    fold=meta.get("fold") if isinstance(meta.get("fold"), int) else None,
                    target_id=meta.get("target") if isinstance(meta.get("target"), int) else None,
                )
            )
        else:
            manifest_label = _read_manifest_label(wav_path)
            if manifest_label:
                records.append(AudioRecord(path=wav_path, label=manifest_label))
            elif allow_target_id_labels:
                target_id = _target_id_from_esc50_filename(wav_path)
                if target_id is not None:
                    records.append(AudioRecord(path=wav_path, label=f"esc50_target_{target_id:02d}", target_id=target_id))
        if max_files is not None and len(records) >= max_files:
            break
    if not records:
        raise RuntimeError("no labeled WAV files found; provide --metadata-csv or manifest labels")
    return records


def _load_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = max(1, handle.getnchannels())
        sample_rate = int(handle.getframerate())
        sample_width = int(handle.getsampwidth())
        frames = handle.readframes(handle.getnframes())
    if sample_width == 1:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise RuntimeError(f"unsupported sample width for {path}: {sample_width}")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data.astype(np.float32), sample_rate


def _resample_linear(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or len(samples) == 0:
        return samples.astype(np.float32)
    duration = len(samples) / max(source_rate, 1)
    target_len = max(1, int(round(duration * target_rate)))
    source_x = np.linspace(0.0, duration, len(samples), endpoint=False)
    target_x = np.linspace(0.0, duration, target_len, endpoint=False)
    return np.interp(target_x, source_x, samples).astype(np.float32)


class Esc50Dataset:
    def __init__(self, records: list[AudioRecord], label_to_index: dict[str, int], sample_rate: int, feature_params: dict[str, Any]):
        self.records = records
        self.label_to_index = label_to_index
        self.sample_rate = sample_rate
        self.feature_params = feature_params
        # Cache deterministic log-mel features so the expensive NAS read + FFT
        # runs once per file, not once per epoch (CPU training speedup).
        self._cache: dict[int, np.ndarray] = {}

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> tuple[Any, Any]:
        import torch

        record = self.records[index]
        cached = self._cache.get(index)
        if cached is None:
            samples, source_rate = _load_wav_mono(record.path)
            samples = _resample_linear(samples, source_rate, self.sample_rate)
            feature = extract_sine_feature_tensor(
                samples,
                self.sample_rate,
                n_fft=int(self.feature_params["n_fft"]),
                hop_length=int(self.feature_params["hop_length"]),
                n_mels=int(self.feature_params["n_mels"]),
                max_frames=int(self.feature_params["max_frames"]),
                window_sec=float(self.feature_params["window_sec"]),
            )
            cached = np.asarray(feature["tensor"][0], dtype=np.float32)
            self._cache[index] = cached
        return torch.from_numpy(cached), torch.tensor(self.label_to_index[record.label], dtype=torch.long)


def _split_records(records: list[AudioRecord], test_fold: int, seed: int) -> tuple[list[AudioRecord], list[AudioRecord]]:
    fold_records = [record for record in records if record.fold is not None]
    if fold_records:
        train = [record for record in records if record.fold != test_fold]
        test = [record for record in records if record.fold == test_fold]
        return train, test
    shuffled = list(records)
    random.Random(seed).shuffle(shuffled)
    split_at = max(1, int(len(shuffled) * 0.8))
    return shuffled[:split_at], shuffled[split_at:]


def _build_model(label_count: int, embedding_dim: int):
    import torch

    class SineEsc50Cnn(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.encoder = torch.nn.Sequential(
                torch.nn.Conv2d(1, 24, kernel_size=3, padding=1),
                torch.nn.BatchNorm2d(24),
                torch.nn.ReLU(),
                torch.nn.MaxPool2d(2),
                torch.nn.Conv2d(24, 48, kernel_size=3, padding=1),
                torch.nn.BatchNorm2d(48),
                torch.nn.ReLU(),
                torch.nn.MaxPool2d(2),
                torch.nn.Conv2d(48, 96, kernel_size=3, padding=1),
                torch.nn.BatchNorm2d(96),
                torch.nn.ReLU(),
                torch.nn.AdaptiveAvgPool2d((1, 1)),
            )
            self.embedding = torch.nn.Linear(96, embedding_dim)
            self.classifier = torch.nn.Linear(embedding_dim, label_count)

        def forward(self, x):  # type: ignore[no-untyped-def]
            encoded = self.encoder(x).flatten(1)
            embedding = torch.relu(self.embedding(encoded))
            logits = self.classifier(embedding)
            return logits, embedding

    return SineEsc50Cnn()


def _evaluate(model: Any, loader: Any, label_count: int, device: str) -> dict[str, Any]:
    import torch

    model.eval()
    confusion = np.zeros((label_count, label_count), dtype=int)
    total = 0
    correct = 0
    with torch.no_grad():
        for features, targets in loader:
            features = features.to(device)
            targets = targets.to(device)
            logits, _embedding = model(features)
            predictions = torch.argmax(logits, dim=1)
            total += int(targets.numel())
            correct += int((predictions == targets).sum().item())
            for truth, pred in zip(targets.cpu().numpy().tolist(), predictions.cpu().numpy().tolist()):
                confusion[int(truth), int(pred)] += 1
    return {
        "accuracy": float(correct / total) if total else 0.0,
        "total": total,
        "correct": correct,
        "confusion_matrix": confusion.tolist(),
    }


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _sql_text_array(values: list[str]) -> str:
    if not values:
        return "'{}'"
    escaped = ",".join('"' + value.replace('"', '\\"') + '"' for value in values)
    return _sql_literal("{" + escaped + "}")


def registry_insert_sql(row: dict[str, Any]) -> str:
    columns = [
        "model_id",
        "model_name",
        "model_version",
        "domain",
        "target_domains",
        "class_families",
        "framework",
        "runtime",
        "artifact_uri",
        "artifact_sha256",
        "label_map_uri",
        "label_map_sha256",
        "training_dataset",
        "metrics_uri",
        "confusion_matrix_uri",
        "input_sample_rate_hz",
        "window_sec",
        "label_count",
        "embedding_dim",
        "device",
        "status",
        "loaded",
        "feature_params",
    ]
    values: list[str] = []
    for column in columns:
        value = row.get(column)
        if column in {"target_domains", "class_families"}:
            values.append(_sql_text_array([str(item) for item in value or []]))
        elif column == "loaded":
            values.append("true" if value else "false")
        elif column == "feature_params":
            values.append(_sql_literal(json.dumps(value or {}, sort_keys=True)) + "::jsonb")
        elif column in {"input_sample_rate_hz", "label_count", "embedding_dim"}:
            values.append(str(int(value)) if value is not None else "NULL")
        elif column == "window_sec":
            values.append(str(float(value)) if value is not None else "NULL")
        else:
            values.append(_sql_literal(value))
    assignments = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns if column != "model_id")
    return (
        "INSERT INTO sine.model_artifact (\n    "
        + ", ".join(columns)
        + "\n) VALUES (\n    "
        + ", ".join(values)
        + "\n)\nON CONFLICT (model_id) DO UPDATE SET "
        + assignments
        + ", updated_at = now();\n"
    )


def train_and_export(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    from torch.utils.data import DataLoader

    feature_params = dict(DEFAULT_FEATURE_PARAMS)
    feature_params.update(
        {
            "n_fft": int(args.n_fft),
            "hop_length": int(args.hop_length),
            "n_mels": int(args.n_mels),
            "max_frames": int(args.max_frames),
            "window_sec": float(args.window_sec),
        }
    )
    records = discover_audio_records(
        Path(args.audio_root),
        metadata_csv=Path(args.metadata_csv) if args.metadata_csv else None,
        max_files=args.max_files,
        allow_target_id_labels=bool(args.allow_target_id_labels),
    )
    labels = sorted({record.label for record in records})
    if len(labels) < 2:
        raise RuntimeError("at least two labels are required for training")
    label_to_index = {label: index for index, label in enumerate(labels)}
    train_records, test_records = _split_records(records, int(args.test_fold), int(args.seed))
    if not train_records or not test_records:
        raise RuntimeError("training and validation splits must both contain records")

    random.seed(int(args.seed))
    np.random.seed(int(args.seed))
    torch.manual_seed(int(args.seed))
    device = "cuda" if args.device == "auto" and torch.cuda.is_available() else ("cpu" if args.device == "auto" else args.device)
    model = _build_model(len(labels), int(args.embedding_dim)).to(device)
    train_loader = DataLoader(
        Esc50Dataset(train_records, label_to_index, int(args.sample_rate), feature_params),
        batch_size=int(args.batch_size),
        shuffle=True,
        num_workers=int(args.num_workers),
    )
    test_loader = DataLoader(
        Esc50Dataset(test_records, label_to_index, int(args.sample_rate), feature_params),
        batch_size=int(args.batch_size),
        shuffle=False,
        num_workers=int(args.num_workers),
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=float(args.learning_rate))
    loss_fn = torch.nn.CrossEntropyLoss()
    history: list[dict[str, Any]] = []
    started = time.time()
    for epoch in range(1, int(args.epochs) + 1):
        model.train()
        total_loss = 0.0
        total_seen = 0
        for features, targets in train_loader:
            features = features.to(device)
            targets = targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits, _embedding = model(features)
            loss = loss_fn(logits, targets)
            loss.backward()
            optimizer.step()
            total_loss += float(loss.item()) * int(targets.numel())
            total_seen += int(targets.numel())
        evaluation = _evaluate(model, test_loader, len(labels), device)
        history.append(
            {
                "epoch": epoch,
                "train_loss": float(total_loss / max(total_seen, 1)),
                "validation_accuracy": evaluation["accuracy"],
            }
        )
        print(
            f"[epoch {epoch}/{int(args.epochs)}] "
            f"train_loss={total_loss / max(total_seen, 1):.4f} "
            f"val_acc={evaluation['accuracy']:.4f} "
            f"elapsed={time.time() - started:.0f}s",
            flush=True,
        )

    output_root = Path(args.output_root)
    package_root = output_root / str(args.model_id)
    package_root.mkdir(parents=True, exist_ok=True)
    model_path = package_root / "model.torchscript.pt"
    label_map_path = package_root / "labels.json"
    metrics_path = package_root / "metrics.json"
    confusion_path = package_root / "confusion_matrix.json"
    manifest_path = package_root / "training_manifest.json"
    registry_path = package_root / "model_registry_row.json"
    sql_path = package_root / "register_model_artifact.sql"

    model_cpu = model.to("cpu").eval()
    example = torch.zeros(1, 1, int(feature_params["n_mels"]), int(feature_params["max_frames"]), dtype=torch.float32)
    traced = torch.jit.trace(model_cpu, example)
    traced.save(str(model_path))

    label_payload = {
        "labels": labels,
        "index_to_label": {str(index): label for index, label in enumerate(labels)},
        "source": "ESC-50",
    }
    label_map_path.write_text(json.dumps(label_payload, indent=2, sort_keys=True), encoding="utf-8")

    final_eval = _evaluate(model_cpu, test_loader, len(labels), "cpu")
    metrics = {
        "model_id": args.model_id,
        "model_name": args.model_name,
        "training_dataset": "ESC-50",
        "train_records": len(train_records),
        "validation_records": len(test_records),
        "label_count": len(labels),
        "epochs": int(args.epochs),
        "history": history,
        "validation_accuracy": final_eval["accuracy"],
        "validation_total": final_eval["total"],
        "validation_correct": final_eval["correct"],
        "elapsed_sec": round(time.time() - started, 3),
    }
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
    confusion_path.write_text(
        json.dumps({"labels": labels, "matrix": final_eval["confusion_matrix"]}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    manifest = {
        "model_id": args.model_id,
        "model_name": args.model_name,
        "model_version": args.model_version,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "audio_root": str(Path(args.audio_root).resolve()),
        "metadata_csv": str(Path(args.metadata_csv).resolve()) if args.metadata_csv else None,
        "feature_params": feature_params,
        "sample_rate_hz": int(args.sample_rate),
        "embedding_dim": int(args.embedding_dim),
        "train_records": len(train_records),
        "validation_records": len(test_records),
        "labels": labels,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    registry_row = {
        "model_id": args.model_id,
        "model_name": args.model_name,
        "model_version": args.model_version,
        "domain": "acoustic",
        "target_domains": ["air"],
        "class_families": ["terrestrial_bioacoustics", "air_propeller", "weather_lightning", "mechanical", "unknown_pattern"],
        "framework": "torchscript",
        "runtime": "torch",
        "artifact_uri": str(model_path.resolve()),
        "artifact_sha256": sha256_file(model_path),
        "label_map_uri": str(label_map_path.resolve()),
        "label_map_sha256": sha256_file(label_map_path),
        "training_dataset": "ESC-50",
        "metrics_uri": str(metrics_path.resolve()),
        "confusion_matrix_uri": str(confusion_path.resolve()),
        "input_sample_rate_hz": int(args.sample_rate),
        "window_sec": float(args.window_sec),
        "label_count": len(labels),
        "embedding_dim": int(args.embedding_dim),
        "device": "cpu",
        "status": "trained",
        "loaded": False,
        "feature_params": feature_params,
    }
    registry_path.write_text(json.dumps(registry_row, indent=2, sort_keys=True), encoding="utf-8")
    sql_path.write_text(registry_insert_sql(registry_row), encoding="utf-8")
    return {
        "status": "artifact_ready",
        "package_root": str(package_root.resolve()),
        "model_path": str(model_path.resolve()),
        "label_map_path": str(label_map_path.resolve()),
        "registry_sql": str(sql_path.resolve()),
        "metrics": metrics,
        "artifact_sha256": registry_row["artifact_sha256"],
        "label_map_sha256": registry_row["label_map_sha256"],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-root", required=True, help="Directory containing real ESC-50 WAV files.")
    parser.add_argument("--metadata-csv", help="Path to ESC-50 meta/esc50.csv. Required unless manifests provide labels.")
    parser.add_argument("--output-root", default="/mnt/nas/mindex/models/acoustic", help="Artifact package output root.")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--model-version", default="p0")
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--allow-target-id-labels", action="store_true", help="Use ESC-50 target ids from filenames if category labels are missing.")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--window-sec", type=float, default=DEFAULT_FEATURE_PARAMS["window_sec"])
    parser.add_argument("--n-fft", type=int, default=DEFAULT_FEATURE_PARAMS["n_fft"])
    parser.add_argument("--hop-length", type=int, default=DEFAULT_FEATURE_PARAMS["hop_length"])
    parser.add_argument("--n-mels", type=int, default=DEFAULT_FEATURE_PARAMS["n_mels"])
    parser.add_argument("--max-frames", type=int, default=DEFAULT_FEATURE_PARAMS["max_frames"])
    parser.add_argument("--embedding-dim", type=int, default=128)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--test-fold", type=int, default=5)
    parser.add_argument("--seed", type=int, default=17805)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    result = train_and_export(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
