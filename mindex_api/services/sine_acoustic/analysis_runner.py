"""Persist real SINE model inference evidence for analysis runs.

This module is the narrow bridge between a registered acoustic model artifact
and the SINE analysis result tables. It deliberately writes no semantic labels
unless ``run_registered_model_inference`` returns a real, provenance-rich model
output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .audio_io import load_mono
from .evidence_builder import persist_evidence_for_model_output
from .inference_runtime import run_registered_model_inference
from .model_runtime import registry_relation_exists
from .prototype_search import extract_query_embedding, run_and_persist_prototype_matches


READY_MODEL_STATES = {"model_ready", "ready", "loaded"}


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _json_dump(value: Any, default: Any) -> str:
    if value is None:
        value = default
    return json.dumps(value)


def build_model_output_insert_params(
    *,
    analysis_run_id: UUID,
    blob_id: UUID,
    inference_result: dict[str, Any],
    window_start_sec: float = 0.0,
    window_end_sec: float | None = None,
) -> dict[str, Any] | None:
    """Build SQL params for ``sine.model_output`` from a proven inference result."""
    if not inference_result.get("ok"):
        return None
    model_id = str(inference_result.get("model_id") or "").strip()
    top_label = str(inference_result.get("top_label") or "").strip()
    artifact_sha = str(inference_result.get("artifact_sha256") or "").strip()
    label_sha = str(inference_result.get("label_map_sha256") or "").strip()
    if not (model_id and top_label and artifact_sha and label_sha):
        return None

    metadata = {
        "status": inference_result.get("status"),
        "model_status": inference_result.get("model_status"),
        "model_name": inference_result.get("model_name"),
        "model_version": inference_result.get("model_version"),
        "framework": inference_result.get("framework"),
        "runtime": inference_result.get("runtime"),
        "artifact_uri": inference_result.get("artifact_uri"),
        "label_map_uri": inference_result.get("label_map_uri"),
        "feature_sha256": inference_result.get("feature_sha256"),
        "feature_metadata": inference_result.get("feature_metadata"),
        "tensor_shape": inference_result.get("tensor_shape"),
        "sample_rate_hz": inference_result.get("sample_rate_hz"),
        "window_sec": inference_result.get("window_sec"),
    }
    return {
        "analysis_run_id": analysis_run_id,
        "blob_id": blob_id,
        "model_id": model_id,
        "output_kind": inference_result.get("output_kind") or "classification",
        "window_start_sec": window_start_sec,
        "window_end_sec": window_end_sec,
        "top_label": top_label,
        "confidence": inference_result.get("confidence"),
        "ood_score": inference_result.get("ood_score"),
        "labels": _json_dump(inference_result.get("labels"), []),
        "scores": _json_dump(inference_result.get("scores"), {}),
        "embedding_ref": inference_result.get("embedding_ref"),
        "embedding_sha256": inference_result.get("embedding_sha256"),
        "artifact_sha256": artifact_sha,
        "label_map_sha256": label_sha,
        "runtime_ms": inference_result.get("runtime_ms"),
        "latency_ms": inference_result.get("latency_ms"),
        "metadata": _json_dump(metadata, {}),
    }


async def select_loaded_acoustic_models(
    db: AsyncSession,
    *,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """Return registered loaded acoustic models in priority order."""
    if not await registry_relation_exists(db, "sine.model_artifact"):
        return []
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    model_id, model_name, model_version, domain, target_domains,
                    class_families, framework, runtime, artifact_uri, artifact_sha256,
                    label_map_uri, label_map_sha256, training_dataset, metrics_uri,
                    confusion_matrix_uri, input_sample_rate_hz, window_sec,
                    label_count, embedding_dim, device, status, loaded,
                    backend_commit, feature_params
                FROM sine.model_artifact
                WHERE domain = 'acoustic'
                  AND (loaded IS TRUE OR lower(status) IN ('model_ready', 'ready', 'loaded'))
                ORDER BY COALESCE(loaded, FALSE) DESC, updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {"limit": max(1, int(limit or 1))},
        )
    ).mappings().all()
    models = [dict(row) for row in rows]
    for model in models:
        model["feature_params"] = _jsonish(model.get("feature_params")) or {}
    return models


async def persist_model_output(
    db: AsyncSession,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Insert one proven model output row and return the persisted id."""
    row = (
        await db.execute(
            text(
                """
                INSERT INTO sine.model_output (
                    analysis_run_id, blob_id, model_id, output_kind,
                    window_start_sec, window_end_sec, top_label, confidence,
                    ood_score, labels, scores, embedding_ref, embedding_sha256,
                    artifact_sha256, label_map_sha256, runtime_ms, latency_ms, metadata
                ) VALUES (
                    :analysis_run_id, :blob_id, :model_id, :output_kind,
                    :window_start_sec, :window_end_sec, :top_label, :confidence,
                    :ood_score, CAST(:labels AS jsonb), CAST(:scores AS jsonb),
                    :embedding_ref, :embedding_sha256, :artifact_sha256,
                    :label_map_sha256, :runtime_ms, :latency_ms, CAST(:metadata AS jsonb)
                )
                RETURNING id::text
                """
            ),
            params,
        )
    ).mappings().first()
    output = dict(params)
    output["id"] = row["id"] if row else None
    output["labels"] = json.loads(str(params["labels"]))
    output["scores"] = json.loads(str(params["scores"]))
    output["metadata"] = json.loads(str(params["metadata"]))
    return output


async def run_and_persist_loaded_model_outputs(
    db: AsyncSession,
    *,
    blob_id: UUID,
    analysis_run_id: UUID,
    wav_path: Path,
    max_models: int = 1,
) -> dict[str, Any]:
    """Run loaded registered models for one blob and persist proven outputs."""
    status: dict[str, Any] = {
        "status": "model_outputs_unavailable",
        "model_status": "model_unavailable",
        "attempted_models": 0,
        "model_outputs_persisted": 0,
        "model_outputs": [],
        "prototype_matches": [],
        "fusion_evidence": [],
        "sound_transcripts": [],
        "blocking_reasons": [],
    }
    if not await registry_relation_exists(db, "sine.model_output"):
        status["blocking_reasons"].append("model_output_table_missing")
        return status

    try:
        models = await select_loaded_acoustic_models(db, limit=max_models)
    except Exception as exc:
        status["blocking_reasons"].append(f"model_registry_query_failed:{exc!s}")
        return status
    if not models:
        status["blocking_reasons"].append("no_loaded_model")
        return status

    status["attempted_models"] = len(models)
    for model in models:
        model_id = str(model.get("model_id") or "unknown")
        try:
            target_sr = int(model.get("input_sample_rate_hz") or 16000)
            samples, sample_rate = load_mono(wav_path, target_sr=target_sr)
            window_end_sec = len(samples) / max(int(sample_rate or 1), 1)
            inference = run_registered_model_inference(samples, sample_rate, model)
            params = build_model_output_insert_params(
                analysis_run_id=analysis_run_id,
                blob_id=blob_id,
                inference_result=inference,
                window_start_sec=0.0,
                window_end_sec=window_end_sec,
            )
            if not params:
                status["blocking_reasons"].append(f"{model_id}:{inference.get('status', 'model_output_not_proven')}")
                await db.execute(
                    text(
                        """
                        UPDATE sine.model_artifact
                        SET last_error = :last_error, updated_at = NOW()
                        WHERE model_id = :model_id
                        """
                    ),
                    {"model_id": model_id, "last_error": inference.get("detail") or inference.get("status")},
                )
                continue
            output = await persist_model_output(db, params)
            status["model_outputs"].append(output)
            query_embedding = extract_query_embedding(inference)
            if query_embedding:
                prototype_result = await run_and_persist_prototype_matches(
                    db,
                    analysis_run_id=analysis_run_id,
                    blob_id=blob_id,
                    model_output=output,
                    query_vector=query_embedding,
                )
                status["prototype_matches"].extend(prototype_result.get("prototype_matches") or [])
                status["blocking_reasons"].extend(prototype_result.get("blocking_reasons") or [])
            derived = await persist_evidence_for_model_output(
                db,
                analysis_run_id=analysis_run_id,
                blob_id=blob_id,
                model_output=output,
            )
            status["fusion_evidence"].extend(derived.get("fusion_evidence") or [])
            status["sound_transcripts"].extend(derived.get("sound_transcripts") or [])
            status["blocking_reasons"].extend(derived.get("blocking_reasons") or [])
            await db.execute(
                text(
                    """
                    UPDATE sine.model_artifact
                    SET last_inference_at = NOW(), last_error = NULL, updated_at = NOW()
                    WHERE model_id = :model_id
                    """
                ),
                {"model_id": model_id},
            )
        except Exception as exc:
            status["blocking_reasons"].append(f"{model_id}:model_inference_failed:{exc!s}")

    status["model_outputs_persisted"] = len(status["model_outputs"])
    if status["model_outputs"]:
        status["status"] = "model_outputs_persisted"
        status["model_status"] = "model_ready"
    return status
