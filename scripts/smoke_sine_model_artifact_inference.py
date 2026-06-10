#!/usr/bin/env python
"""Runtime-smoke a verified SINE acoustic model artifact package.

This is the second gate after ``verify_sine_model_artifact_package.py``.
It decodes a real WAV file, runs the local TorchScript/ONNX inference seam,
and writes optional SQL that marks the model loaded only after inference
succeeds. It does not update Postgres by itself.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from mindex_api.services.sine_acoustic.audio_io import load_mono
from mindex_api.services.sine_acoustic.inference_runtime import run_registered_model_inference


VERIFY_SCRIPT = Path(__file__).resolve().with_name("verify_sine_model_artifact_package.py")
BLOCKING_OOD_STATUSES = {"low_confidence", "out_of_domain", "out_of_domain_candidate"}


def _load_verifier_module() -> Any:
    spec = importlib.util.spec_from_file_location("verify_sine_model_artifact_package", VERIFY_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"could not load verifier script: {VERIFY_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def mark_model_loaded_sql(model_row: dict[str, Any], inference_result: dict[str, Any]) -> str:
    """Return guarded SQL for marking a model loaded after runtime proof."""
    model_id = str(model_row.get("model_id") or inference_result.get("model_id") or "").strip()
    artifact_sha = str(inference_result.get("artifact_sha256") or model_row.get("artifact_sha256") or "").strip()
    label_sha = str(inference_result.get("label_map_sha256") or model_row.get("label_map_sha256") or "").strip()
    if not (model_id and artifact_sha and label_sha):
        raise ValueError("model_id, artifact_sha256, and label_map_sha256 are required")
    details = {
        "smoke_status": inference_result.get("status"),
        "top_label": inference_result.get("top_label"),
        "confidence": inference_result.get("confidence"),
        "ood_score": inference_result.get("ood_score"),
        "ood_status": inference_result.get("ood_status"),
        "runtime": inference_result.get("runtime"),
        "runtime_ms": inference_result.get("runtime_ms"),
        "embedding_sha256": inference_result.get("embedding_sha256"),
        "feature_sha256": inference_result.get("feature_sha256"),
        "tensor_shape": inference_result.get("tensor_shape"),
        "sample_rate_hz": inference_result.get("sample_rate_hz"),
    }
    return (
        "UPDATE sine.model_artifact\n"
        "SET status = 'model_ready',\n"
        "    loaded = TRUE,\n"
        "    last_loaded_at = NOW(),\n"
        "    last_inference_at = NOW(),\n"
        "    last_error = NULL,\n"
        "    feature_params = COALESCE(feature_params, '{}'::jsonb) || "
        + _sql_literal(json.dumps({"runtime_smoke": details}, sort_keys=True))
        + "::jsonb,\n"
        "    updated_at = NOW()\n"
        "WHERE model_id = "
        + _sql_literal(model_id)
        + "\n  AND artifact_sha256 = "
        + _sql_literal(artifact_sha)
        + "\n  AND label_map_sha256 = "
        + _sql_literal(label_sha)
        + ";\n"
    )


def _safe_inference_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Drop raw embedding vectors from reports while preserving proof fields."""
    safe = dict(result)
    if "embedding" in safe:
        safe["embedding"] = f"<{len(safe.get('embedding') or [])} floats omitted>"
    return safe


def smoke_package_inference(
    package_root: Path,
    wav_path: Path,
    *,
    expected_model_id: str | None = None,
    top_k: int = 5,
    fail_on_ood: bool = True,
) -> dict[str, Any]:
    verifier = _load_verifier_module()
    verification = verifier.verify_package(package_root, expected_model_id=expected_model_id)
    if verification.get("status") != "verified":
        return {
            "status": "artifact_verification_failed",
            "ok": False,
            "verification": verification,
            "inference": None,
            "loaded_sql": None,
        }

    package_root = package_root.resolve()
    wav_path = wav_path.resolve()
    if not wav_path.is_file():
        return {
            "status": "wav_missing",
            "ok": False,
            "verification": verification,
            "wav_path": str(wav_path),
            "inference": None,
            "loaded_sql": None,
        }
    model_row = _read_json(package_root / "model_registry_row.json")
    target_sr = int(model_row.get("input_sample_rate_hz") or 16000)
    samples, sample_rate = load_mono(wav_path, target_sr=target_sr)
    inference = run_registered_model_inference(samples, sample_rate, model_row, top_k=top_k)
    safe_inference = _safe_inference_payload(inference)
    if not inference.get("ok"):
        return {
            "status": "runtime_smoke_failed",
            "ok": False,
            "verification": verification,
            "wav_path": str(wav_path),
            "sample_rate_hz": int(sample_rate),
            "sample_count": int(len(samples)),
            "inference": safe_inference,
            "loaded_sql": None,
        }
    if fail_on_ood and str(inference.get("ood_status") or "").lower() in BLOCKING_OOD_STATUSES:
        return {
            "status": "runtime_smoke_ood_review",
            "ok": False,
            "verification": verification,
            "wav_path": str(wav_path),
            "sample_rate_hz": int(sample_rate),
            "sample_count": int(len(samples)),
            "inference": safe_inference,
            "loaded_sql": None,
        }
    loaded_sql = mark_model_loaded_sql(model_row, inference)
    return {
        "status": "runtime_smoke_passed",
        "ok": True,
        "verification": verification,
        "wav_path": str(wav_path),
        "sample_rate_hz": int(sample_rate),
        "sample_count": int(len(samples)),
        "inference": safe_inference,
        "loaded_sql": loaded_sql,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", required=True, help="Verified model artifact package directory.")
    parser.add_argument("--wav-path", required=True, help="Real WAV file used for runtime smoke inference.")
    parser.add_argument("--expected-model-id", help="Optional model_id that must match the package.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--allow-ood", action="store_true", help="Allow low-confidence/OOD smoke output to pass.")
    parser.add_argument("--write-report", help="Optional JSON report path.")
    parser.add_argument("--write-loaded-sql", help="Optional path for generated mark-loaded SQL.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    report = smoke_package_inference(
        Path(args.package_root),
        Path(args.wav_path),
        expected_model_id=args.expected_model_id,
        top_k=int(args.top_k),
        fail_on_ood=not bool(args.allow_ood),
    )
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.write_report:
        Path(args.write_report).write_text(output + "\n", encoding="utf-8")
    if args.write_loaded_sql and report.get("ok") and report.get("loaded_sql"):
        Path(args.write_loaded_sql).write_text(str(report["loaded_sql"]), encoding="utf-8")
    print(output)
    if not report.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
