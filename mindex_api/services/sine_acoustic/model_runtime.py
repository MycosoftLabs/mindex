"""Honest SINE acoustic model runtime inspection.

This module is intentionally conservative. It does not classify audio and does
not emit labels. It only reports whether MINDEX has the registry/runtime pieces
needed before a future PyTorch/TorchScript/ONNX implementation may emit model
outputs.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


READY_MODEL_STATES = {"model_ready", "ready", "loaded"}
SUPPORTED_RUNTIMES = {"pytorch", "torch", "torchscript", "onnx", "onnxruntime"}


def runtime_backend_status() -> dict[str, bool]:
    """Return optional ML runtime availability without importing heavy modules."""
    return {
        "torch": importlib.util.find_spec("torch") is not None,
        "onnxruntime": importlib.util.find_spec("onnxruntime") is not None,
    }


def _normalize_runtime(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "")


def _runtime_supported(runtime: Any, backends: dict[str, bool]) -> bool:
    normalized = _normalize_runtime(runtime)
    if normalized in {"pytorch", "torch", "torchscript"}:
        return backends["torch"]
    if normalized in {"onnx", "onnxruntime"}:
        return backends["onnxruntime"]
    return False


def artifact_path_from_uri(artifact_uri: str | None) -> Path | None:
    if not artifact_uri:
        return None
    raw = artifact_uri.strip()
    if not raw or raw.startswith(("http://", "https://", "s3://")):
        return None
    if raw.startswith("file://"):
        raw = raw[7:]
    return Path(raw)


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def registry_relation_exists(db: AsyncSession, relation: str) -> bool:
    try:
        return bool((await db.execute(text("SELECT to_regclass(:relation)"), {"relation": relation})).scalar())
    except Exception:
        await db.rollback()
        return False


async def inspect_sine_model_runtime(
    db: AsyncSession,
    *,
    request_contract: dict[str, Any] | None = None,
    verify_artifacts: bool = False,
) -> dict[str, Any]:
    """Inspect registered model/prototype state without performing inference."""
    backends = runtime_backend_status()
    contract = request_contract or {}
    context: dict[str, Any] = {
        "model_status": "model_unavailable",
        "model_ready": False,
        "model_registry_ready": False,
        "prototype_catalog_ready": False,
        "registered_models": 0,
        "loaded_models": 0,
        "runtime_backends": backends,
        "runtime_supported": False,
        "inference_ready": False,
        "artifact_verified": False,
        "blocking_reasons": [],
        "request_contract_status": contract.get("status"),
    }

    if not await registry_relation_exists(db, "sine.model_artifact"):
        context["blocking_reasons"].append("model_registry_missing")
        if await registry_relation_exists(db, "sine.prototype"):
            proto_count = (
                await db.execute(text("SELECT COUNT(*)::int FROM sine.prototype"))
            ).scalar() or 0
            context["prototype_catalog_ready"] = int(proto_count) > 0
        else:
            context["blocking_reasons"].append("prototype_catalog_missing")
        return context

    rows = (
        await db.execute(
            text(
                """
                SELECT model_id, model_name, model_version, framework, runtime,
                       artifact_uri, artifact_sha256, status, loaded
                FROM sine.model_artifact
                ORDER BY COALESCE(loaded, FALSE) DESC, updated_at DESC NULLS LAST
                """
            )
        )
    ).mappings().all()
    context["registered_models"] = len(rows)
    context["model_registry_ready"] = bool(rows)
    loaded_rows = [
        dict(row)
        for row in rows
        if bool(row.get("loaded")) or str(row.get("status") or "").lower() in READY_MODEL_STATES
    ]
    context["loaded_models"] = len(loaded_rows)

    if not rows:
        context["blocking_reasons"].append("model_registry_empty")
    if not loaded_rows:
        context["blocking_reasons"].append("no_loaded_model")

    runtime_supported = any(_runtime_supported(row.get("runtime"), backends) for row in loaded_rows)
    context["runtime_supported"] = runtime_supported
    if loaded_rows and not runtime_supported:
        context["model_status"] = "model_runtime_unavailable"
        context["blocking_reasons"].append("runtime_dependency_missing")

    verified = False
    if verify_artifacts and loaded_rows:
        verified = True
        for row in loaded_rows:
            path = artifact_path_from_uri(str(row.get("artifact_uri") or ""))
            expected = str(row.get("artifact_sha256") or "").strip().lower()
            if not path or not path.exists():
                verified = False
                context["blocking_reasons"].append(f"artifact_missing:{row.get('model_id')}")
                continue
            if expected and sha256_file(path).lower() != expected:
                verified = False
                context["blocking_reasons"].append(f"artifact_checksum_mismatch:{row.get('model_id')}")
        context["artifact_verified"] = verified

    if await registry_relation_exists(db, "sine.prototype"):
        proto_count = (
            await db.execute(text("SELECT COUNT(*)::int FROM sine.prototype"))
        ).scalar() or 0
        context["prototype_catalog_ready"] = int(proto_count) > 0
        if not context["prototype_catalog_ready"]:
            context["blocking_reasons"].append("prototype_catalog_empty")
    else:
        context["blocking_reasons"].append("prototype_catalog_missing")

    # A registry row and installed runtime are still not a completed classifier.
    # inference_ready requires PROOF a real model ran: at least one persisted
    # sine.model_output row with artifact/checksum provenance.
    if loaded_rows and runtime_supported:
        context["model_status"] = "model_runtime_available"

    model_output_count = 0
    if await registry_relation_exists(db, "sine.model_output"):
        model_output_count = int(
            (await db.execute(text("SELECT COUNT(*)::int FROM sine.model_output"))).scalar() or 0
        )
    context["model_output_count"] = model_output_count

    fully_ready = bool(
        loaded_rows
        and runtime_supported
        and context.get("prototype_catalog_ready")
        and model_output_count > 0
        and (not verify_artifacts or context.get("artifact_verified"))
    )
    if fully_ready:
        context["inference_ready"] = True
        context["model_ready"] = True
        context["model_status"] = "model_ready"
    elif loaded_rows and runtime_supported and model_output_count == 0:
        context["blocking_reasons"].append("no_persisted_model_output")

    if not context["blocking_reasons"] and not fully_ready:
        context["blocking_reasons"].append("model_inference_not_implemented")
    return context
