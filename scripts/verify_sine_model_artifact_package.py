#!/usr/bin/env python
"""Verify a SINE acoustic model artifact package before DB registration.

This is a pre-registration gate. It does not load the model, mark a model
ready, or write to Postgres. It only proves the package built by
train_sine_esc50_p0.py is internally consistent enough for Cursor to inspect,
register, and then runtime-test on VM 189.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from mindex_api.services.sine_acoustic.model_runtime import artifact_path_from_uri, sha256_file


REQUIRED_FILES = {
    "model": "model.torchscript.pt",
    "labels": "labels.json",
    "metrics": "metrics.json",
    "confusion_matrix": "confusion_matrix.json",
    "training_manifest": "training_manifest.json",
    "registry_row": "model_registry_row.json",
    "registry_sql": "register_model_artifact.sql",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _package_path(package_root: Path, uri: str | None, fallback_name: str) -> Path:
    """Resolve an artifact URI, falling back to the local package filename."""
    path = artifact_path_from_uri(uri or "")
    if path and path.exists():
        return path
    if path and (package_root / path.name).exists():
        return package_root / path.name
    return package_root / fallback_name


def _labels_from_payload(payload: Any) -> list[str]:
    if isinstance(payload, list):
        return [str(item) for item in payload]
    if isinstance(payload, dict):
        if isinstance(payload.get("labels"), list):
            return [str(item) for item in payload["labels"]]
        if isinstance(payload.get("classes"), list):
            return [str(item) for item in payload["classes"]]
        pairs: list[tuple[int, str]] = []
        for key, value in payload.items():
            try:
                pairs.append((int(key), str(value)))
            except (TypeError, ValueError):
                continue
        if pairs:
            return [value for _, value in sorted(pairs)]
    return []


def _record_check(checks: list[dict[str, Any]], name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _confusion_matrix_is_square(payload: Any, label_count: int) -> bool:
    if not isinstance(payload, dict):
        return False
    matrix = payload.get("matrix")
    if not isinstance(matrix, list) or len(matrix) != label_count:
        return False
    for row in matrix:
        if not isinstance(row, list) or len(row) != label_count:
            return False
        for value in row:
            try:
                int(value)
            except (TypeError, ValueError):
                return False
    return True


def _feature_params_complete(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"n_fft", "hop_length", "n_mels", "max_frames", "window_sec"}
    return required.issubset(set(value))


def verify_package(
    package_root: Path,
    *,
    expected_model_id: str | None = None,
    min_validation_accuracy: float | None = None,
) -> dict[str, Any]:
    package_root = package_root.resolve()
    checks: list[dict[str, Any]] = []

    for key, filename in REQUIRED_FILES.items():
        path = package_root / filename
        _record_check(checks, f"file:{key}", path.exists(), str(path))

    if not all(check["ok"] for check in checks):
        return {
            "status": "failed",
            "package_root": str(package_root),
            "checks": checks,
            "failures": [check for check in checks if not check["ok"]],
        }

    registry_row = _read_json(package_root / REQUIRED_FILES["registry_row"])
    labels_payload = _read_json(package_root / REQUIRED_FILES["labels"])
    metrics = _read_json(package_root / REQUIRED_FILES["metrics"])
    manifest = _read_json(package_root / REQUIRED_FILES["training_manifest"])
    confusion = _read_json(package_root / REQUIRED_FILES["confusion_matrix"])
    registry_sql = (package_root / REQUIRED_FILES["registry_sql"]).read_text(encoding="utf-8")
    labels = _labels_from_payload(labels_payload)

    model_id = str(registry_row.get("model_id") or "").strip()
    _record_check(checks, "model_id.present", bool(model_id), model_id or "missing")
    if expected_model_id:
        _record_check(checks, "model_id.expected", model_id == expected_model_id, expected_model_id)

    framework = str(registry_row.get("framework") or "").strip().lower()
    runtime = str(registry_row.get("runtime") or "").strip().lower()
    _record_check(checks, "framework.torchscript", framework == "torchscript", framework)
    _record_check(checks, "runtime.torch", runtime in {"torch", "pytorch", "torchscript"}, runtime)
    _record_check(checks, "loaded.false", registry_row.get("loaded") is False, str(registry_row.get("loaded")))
    _record_check(
        checks,
        "status.not_model_ready",
        str(registry_row.get("status") or "").lower() not in {"loaded", "ready", "model_ready"},
        str(registry_row.get("status") or ""),
    )

    artifact_path = _package_path(package_root, registry_row.get("artifact_uri"), REQUIRED_FILES["model"])
    label_map_path = _package_path(package_root, registry_row.get("label_map_uri"), REQUIRED_FILES["labels"])
    _record_check(checks, "artifact.exists", artifact_path.exists(), str(artifact_path))
    _record_check(checks, "artifact.nonempty", artifact_path.exists() and artifact_path.stat().st_size > 0, str(artifact_path))
    _record_check(checks, "label_map.exists", label_map_path.exists(), str(label_map_path))

    if artifact_path.exists():
        actual = sha256_file(artifact_path)
        expected = str(registry_row.get("artifact_sha256") or "").strip().lower()
        _record_check(checks, "artifact.sha256", bool(expected) and actual.lower() == expected, actual)
    if label_map_path.exists():
        actual = sha256_file(label_map_path)
        expected = str(registry_row.get("label_map_sha256") or "").strip().lower()
        _record_check(checks, "label_map.sha256", bool(expected) and actual.lower() == expected, actual)

    label_count = int(registry_row.get("label_count") or 0)
    _record_check(checks, "labels.nonempty", bool(labels), str(len(labels)))
    _record_check(checks, "labels.unique", len(labels) == len(set(labels)), str(len(set(labels))))
    _record_check(checks, "labels.count_matches_registry", bool(labels) and len(labels) == label_count, str(label_count))
    _record_check(checks, "metrics.model_id", metrics.get("model_id") == model_id, str(metrics.get("model_id")))
    _record_check(checks, "manifest.model_id", manifest.get("model_id") == model_id, str(manifest.get("model_id")))
    _record_check(checks, "metrics.label_count", int(metrics.get("label_count") or 0) == label_count, str(metrics.get("label_count")))
    _record_check(checks, "manifest.labels", len(manifest.get("labels") or []) == label_count, str(len(manifest.get("labels") or [])))
    _record_check(checks, "confusion.labels", len(confusion.get("labels") or []) == label_count, str(len(confusion.get("labels") or [])))
    _record_check(checks, "confusion.matrix_square", _confusion_matrix_is_square(confusion, label_count), f"{label_count}x{label_count}")

    train_records = _positive_int(metrics.get("train_records") or manifest.get("train_records"))
    validation_records = _positive_int(metrics.get("validation_records") or manifest.get("validation_records"))
    validation_total = _positive_int(metrics.get("validation_total"))
    validation_correct = _positive_int(metrics.get("validation_correct"))
    _record_check(checks, "metrics.train_records_positive", train_records > 0, str(train_records))
    _record_check(checks, "metrics.validation_records_positive", validation_records > 0, str(validation_records))
    _record_check(checks, "metrics.validation_total_matches", validation_records > 0 and validation_total == validation_records, f"{validation_total} == {validation_records}")
    _record_check(checks, "metrics.validation_correct_in_range", validation_total > 0 and 0 <= validation_correct <= validation_total, f"{validation_correct}/{validation_total}")

    validation_accuracy = metrics.get("validation_accuracy")
    _record_check(checks, "metrics.validation_accuracy_present", validation_accuracy is not None, str(validation_accuracy))
    if min_validation_accuracy is not None and validation_accuracy is not None:
        _record_check(
            checks,
            "metrics.validation_accuracy_min",
            float(validation_accuracy) >= float(min_validation_accuracy),
            f"{validation_accuracy} >= {min_validation_accuracy}",
        )

    _record_check(
        checks,
        "sql.insert_model_artifact",
        "INSERT INTO sine.model_artifact" in registry_sql and "ON CONFLICT (model_id) DO UPDATE" in registry_sql,
        REQUIRED_FILES["registry_sql"],
    )
    _record_check(checks, "sql.artifact_sha", str(registry_row.get("artifact_sha256") or "") in registry_sql, "artifact_sha256")
    _record_check(checks, "sql.label_map_sha", str(registry_row.get("label_map_sha256") or "") in registry_sql, "label_map_sha256")
    _record_check(checks, "target_domains.nonempty", bool(registry_row.get("target_domains")), str(registry_row.get("target_domains")))
    _record_check(checks, "class_families.nonempty", bool(registry_row.get("class_families")), str(registry_row.get("class_families")))
    _record_check(checks, "feature_params.complete", _feature_params_complete(registry_row.get("feature_params")), str(registry_row.get("feature_params")))

    failures = [check for check in checks if not check["ok"]]
    return {
        "status": "verified" if not failures else "failed",
        "package_root": str(package_root),
        "model_id": model_id,
        "artifact_path": str(artifact_path),
        "label_map_path": str(label_map_path),
        "label_count": len(labels),
        "validation_accuracy": validation_accuracy,
        "checks": checks,
        "failures": failures,
        "next_steps": [
            "inspect metrics and confusion_matrix",
            "apply register_model_artifact.sql only if this verifier passes",
            "runtime-load the artifact and mark loaded only after checksum and inference pass",
            "run a UUID-backed acoustic blob through /api/mindex/sine/blobs/{id}/analyze",
        ],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", required=True, help="Model artifact package directory.")
    parser.add_argument("--expected-model-id", help="Optional model_id that must match model_registry_row.json.")
    parser.add_argument("--min-validation-accuracy", type=float, default=None)
    parser.add_argument("--write-report", help="Optional JSON report path.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    report = verify_package(
        Path(args.package_root),
        expected_model_id=args.expected_model_id,
        min_validation_accuracy=args.min_validation_accuracy,
    )
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.write_report:
        Path(args.write_report).write_text(output + "\n", encoding="utf-8")
    print(output)
    if report["status"] != "verified":
        sys.exit(1)


if __name__ == "__main__":
    main()
