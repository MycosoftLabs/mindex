#!/usr/bin/env python
"""Build SINE acoustic prototype vectors from a verified model artifact.

This script creates a prototype/fingerprint catalog from real labeled WAV
files. It does not write to Postgres directly. It runs the registered model
runtime on each labeled clip, averages valid embedding vectors per label, and
writes JSON plus guarded SQL for ``sine.prototype``.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from mindex_api.services.sine_acoustic.audio_io import load_mono
from mindex_api.services.sine_acoustic.inference_runtime import run_registered_model_inference
from mindex_api.services.sine_acoustic.prototype_search import vector_sha256


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
            rows[filename] = dict(row)
    return rows


def _read_manifest_label(wav_path: Path) -> str | None:
    for candidate in (wav_path.with_suffix(wav_path.suffix + ".manifest.json"), wav_path.with_suffix(".manifest.json")):
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


def discover_labeled_wavs(
    audio_root: Path,
    *,
    metadata_csv: Path | None = None,
    max_files: int | None = None,
) -> list[dict[str, Any]]:
    metadata = _read_esc50_metadata(metadata_csv)
    rows: list[dict[str, Any]] = []
    for wav_path in sorted(audio_root.rglob("*.wav")):
        label = None
        meta = metadata.get(wav_path.name)
        if meta:
            label = str(meta.get("category") or "").strip()
        if not label:
            label = _read_manifest_label(wav_path)
        if label:
            rows.append({"path": wav_path, "label": label, "metadata": meta or {}})
        if max_files is not None and len(rows) >= max_files:
            break
    if not rows:
        raise RuntimeError("no labeled WAV files found; provide --metadata-csv or manifest labels")
    return rows


def prototype_id_for_label(model_id: str, label: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "-" for ch in label.strip().lower()).strip("-")
    normalized = "-".join(part for part in normalized.split("-") if part)
    return f"{model_id}--{normalized or 'unknown'}"


def prototype_insert_sql(prototypes: list[dict[str, Any]]) -> str:
    statements: list[str] = []
    for proto in prototypes:
        metadata = dict(proto.get("metadata") or {})
        metadata["centroid"] = proto["vector"]
        metadata["prototype_vector"] = proto["vector"]
        statements.append(
            "INSERT INTO sine.prototype (\n"
            "    prototype_id, label, domain, category, source, source_uri, license,\n"
            "    model_id, embedding_dim, vector_sha256, prototype_sha256,\n"
            "    example_count, metadata\n"
            ") VALUES (\n"
            f"    {_sql_literal(proto['prototype_id'])}, {_sql_literal(proto['label'])}, 'acoustic', {_sql_literal(proto.get('category'))},\n"
            f"    {_sql_literal(proto.get('source'))}, {_sql_literal(proto.get('source_uri'))}, {_sql_literal(proto.get('license'))},\n"
            f"    {_sql_literal(proto.get('model_id'))}, {int(proto['embedding_dim'])}, {_sql_literal(proto['vector_sha256'])}, {_sql_literal(proto['prototype_sha256'])},\n"
            f"    {int(proto['example_count'])}, {_sql_literal(json.dumps(metadata, sort_keys=True))}::jsonb\n"
            ")\n"
            "ON CONFLICT (prototype_id) DO UPDATE SET\n"
            "    label = EXCLUDED.label,\n"
            "    category = EXCLUDED.category,\n"
            "    source = EXCLUDED.source,\n"
            "    source_uri = EXCLUDED.source_uri,\n"
            "    license = EXCLUDED.license,\n"
            "    model_id = EXCLUDED.model_id,\n"
            "    embedding_dim = EXCLUDED.embedding_dim,\n"
            "    vector_sha256 = EXCLUDED.vector_sha256,\n"
            "    prototype_sha256 = EXCLUDED.prototype_sha256,\n"
            "    example_count = EXCLUDED.example_count,\n"
            "    metadata = EXCLUDED.metadata,\n"
            "    updated_at = NOW();"
        )
    return "\n\n".join(statements) + ("\n" if statements else "")


def _finite_embedding(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    vector: list[float] = []
    for item in value:
        try:
            number = float(item)
        except (TypeError, ValueError):
            return []
        if not np.isfinite(number):
            return []
        vector.append(number)
    return vector


def build_prototypes(
    package_root: Path,
    audio_root: Path,
    *,
    metadata_csv: Path | None = None,
    expected_model_id: str | None = None,
    max_files: int | None = None,
    min_examples_per_label: int = 1,
    fail_on_ood: bool = True,
    source: str = "MINDEX acoustic library",
    source_uri: str | None = None,
    license_name: str = "internal",
) -> dict[str, Any]:
    verifier = _load_verifier_module()
    verification = verifier.verify_package(package_root, expected_model_id=expected_model_id)
    if verification.get("status") != "verified":
        return {"status": "artifact_verification_failed", "ok": False, "verification": verification, "prototypes": []}

    package_root = package_root.resolve()
    model_row = _read_json(package_root / "model_registry_row.json")
    model_id = str(model_row.get("model_id") or expected_model_id or "").strip()
    if not model_id:
        return {"status": "model_id_missing", "ok": False, "verification": verification, "prototypes": []}

    labeled = discover_labeled_wavs(audio_root, metadata_csv=metadata_csv, max_files=max_files)
    target_sr = int(model_row.get("input_sample_rate_hz") or 16000)
    vectors_by_label: dict[str, list[list[float]]] = defaultdict(list)
    examples_by_label: dict[str, list[str]] = defaultdict(list)
    skipped: list[dict[str, Any]] = []
    for item in labeled:
        wav_path = Path(item["path"])
        label = str(item["label"])
        try:
            samples, sample_rate = load_mono(wav_path, target_sr=target_sr)
            result = run_registered_model_inference(samples, sample_rate, model_row, top_k=5)
        except Exception as exc:
            skipped.append({"path": str(wav_path), "label": label, "reason": f"inference_exception:{exc!s}"})
            continue
        if not result.get("ok"):
            skipped.append({"path": str(wav_path), "label": label, "reason": str(result.get("status") or "inference_failed")})
            continue
        if fail_on_ood and str(result.get("ood_status") or "").lower() in BLOCKING_OOD_STATUSES:
            skipped.append({"path": str(wav_path), "label": label, "reason": str(result.get("ood_status"))})
            continue
        embedding = _finite_embedding(result.get("embedding"))
        if not embedding:
            skipped.append({"path": str(wav_path), "label": label, "reason": "embedding_missing"})
            continue
        vectors_by_label[label].append(embedding)
        examples_by_label[label].append(str(wav_path))

    prototypes: list[dict[str, Any]] = []
    for label, vectors in sorted(vectors_by_label.items()):
        if len(vectors) < max(1, int(min_examples_per_label or 1)):
            skipped.append({"label": label, "reason": "not_enough_examples", "example_count": len(vectors)})
            continue
        lengths = {len(vector) for vector in vectors}
        if len(lengths) != 1:
            skipped.append({"label": label, "reason": "embedding_dim_mismatch", "dims": sorted(lengths)})
            continue
        matrix = np.asarray(vectors, dtype=np.float32)
        centroid = matrix.mean(axis=0).astype(np.float32)
        vector = centroid.tolist()
        vector_sha = vector_sha256(vector)
        proto = {
            "prototype_id": prototype_id_for_label(model_id, label),
            "label": label,
            "category": label,
            "source": source,
            "source_uri": source_uri or str(audio_root.resolve()),
            "license": license_name,
            "model_id": model_id,
            "embedding_dim": len(vector),
            "vector": vector,
            "vector_sha256": vector_sha,
            "prototype_sha256": vector_sha,
            "example_count": len(vectors),
            "examples": examples_by_label[label],
            "metadata": {
                "builder": "build_sine_prototype_catalog.py",
                "model_id": model_id,
                "artifact_sha256": model_row.get("artifact_sha256"),
                "label_map_sha256": model_row.get("label_map_sha256"),
                "embedding_dim": len(vector),
                "example_count": len(vectors),
                "source_examples": examples_by_label[label],
                "semantic_fallback_used": False,
                "llm_fallback_used": False,
                "filename_fallback_used": False,
                "metadata_fallback_used": False,
            },
        }
        prototypes.append(proto)

    return {
        "status": "prototype_catalog_ready" if prototypes else "prototype_catalog_empty",
        "ok": bool(prototypes),
        "verification": verification,
        "model_id": model_id,
        "prototype_count": len(prototypes),
        "labeled_files": len(labeled),
        "skipped": skipped,
        "prototypes": prototypes,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--audio-root", required=True)
    parser.add_argument("--metadata-csv")
    parser.add_argument("--expected-model-id")
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--min-examples-per-label", type=int, default=1)
    parser.add_argument("--allow-ood", action="store_true")
    parser.add_argument("--source", default="MINDEX acoustic library")
    parser.add_argument("--source-uri")
    parser.add_argument("--license", default="internal")
    parser.add_argument("--write-json", help="Optional prototype catalog JSON path.")
    parser.add_argument("--write-sql", help="Optional SQL path for sine.prototype upserts.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    report = build_prototypes(
        Path(args.package_root),
        Path(args.audio_root),
        metadata_csv=Path(args.metadata_csv) if args.metadata_csv else None,
        expected_model_id=args.expected_model_id,
        max_files=args.max_files,
        min_examples_per_label=int(args.min_examples_per_label),
        fail_on_ood=not bool(args.allow_ood),
        source=args.source,
        source_uri=args.source_uri,
        license_name=getattr(args, "license"),
    )
    if args.write_json:
        Path(args.write_json).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.write_sql and report.get("ok"):
        Path(args.write_sql).write_text(prototype_insert_sql(report["prototypes"]), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "prototypes"}, indent=2, sort_keys=True))
    if not report.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
