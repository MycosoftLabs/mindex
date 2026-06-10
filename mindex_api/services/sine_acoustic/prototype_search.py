"""SINE acoustic prototype matching.

This module performs deterministic vector matching only. It does not classify
from filenames, metadata labels, or detector rows. A match requires a real
query embedding vector and a stored prototype vector.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .model_runtime import registry_relation_exists


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def vector_from_value(value: Any) -> list[float]:
    """Return a finite float vector from common embedding payload shapes."""
    raw = _jsonish(value)
    if isinstance(raw, np.ndarray):
        raw = raw.astype(np.float32).reshape(-1).tolist()
    if isinstance(raw, dict):
        for key in ("vector", "embedding", "values", "centroid", "prototype_vector"):
            if key in raw:
                return vector_from_value(raw[key])
    if not isinstance(raw, list):
        return []
    vector: list[float] = []
    for item in raw:
        try:
            number = float(item)
        except (TypeError, ValueError):
            return []
        if not np.isfinite(number):
            return []
        vector.append(number)
    return vector


def vector_sha256(vector: list[float]) -> str:
    arr = np.asarray(vector, dtype=np.float32)
    return hashlib.sha256(arr.tobytes()).hexdigest()


def cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 0:
        return None
    return float(np.dot(a, b) / denom)


def _prototype_vector(row: dict[str, Any]) -> list[float]:
    metadata = _jsonish(row.get("metadata")) if row.get("metadata") is not None else {}
    if not isinstance(metadata, dict):
        metadata = {}
    for key in ("vector", "embedding", "centroid", "prototype_vector"):
        vector = vector_from_value(metadata.get(key))
        if vector:
            return vector
    return vector_from_value(row.get("vector"))


def extract_query_embedding(inference_result: dict[str, Any]) -> list[float]:
    for key in ("embedding", "embedding_vector", "feature_embedding", "vector"):
        vector = vector_from_value(inference_result.get(key))
        if vector:
            return vector
    embedding = inference_result.get("embedding_output")
    if isinstance(embedding, dict):
        return vector_from_value(embedding)
    return []


async def select_candidate_prototypes(
    db: AsyncSession,
    *,
    model_id: str | None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    if not await registry_relation_exists(db, "sine.prototype"):
        return []
    clauses = ["domain = 'acoustic'"]
    params: dict[str, Any] = {"limit": max(1, int(limit or 1))}
    if model_id:
        clauses.append("(model_id IS NULL OR model_id = :model_id)")
        params["model_id"] = model_id
    where = " AND ".join(clauses)
    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    prototype_id, label, domain, category, source, source_uri,
                    license, model_id, embedding_dim, vector_sha256,
                    prototype_sha256, metadata
                FROM sine.prototype
                WHERE {where}
                ORDER BY example_count DESC NULLS LAST, updated_at DESC NULLS LAST
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def build_prototype_match_insert_params(
    *,
    analysis_run_id: UUID,
    blob_id: UUID,
    model_output: dict[str, Any],
    prototype: dict[str, Any],
    query_vector: list[float],
    prototype_vector: list[float],
    score: float,
) -> dict[str, Any] | None:
    if not query_vector or not prototype_vector:
        return None
    model_output_id = str(model_output.get("id") or "").strip()
    prototype_id = str(prototype.get("prototype_id") or "").strip()
    label = str(prototype.get("label") or "").strip()
    if not (model_output_id and prototype_id and label):
        return None
    query_sha = vector_sha256(query_vector)
    proto_sha = vector_sha256(prototype_vector)
    distance = 1.0 - float(score)
    return {
        "analysis_run_id": analysis_run_id,
        "blob_id": blob_id,
        "model_output_id": model_output_id,
        "prototype_id": prototype_id,
        "label": label,
        "category": prototype.get("category"),
        "source": prototype.get("source"),
        "source_uri": prototype.get("source_uri"),
        "license": prototype.get("license"),
        "score": float(score),
        "distance": distance,
        "segment_start_sec": model_output.get("window_start_sec"),
        "segment_end_sec": model_output.get("window_end_sec"),
        "vector_sha256": query_sha,
        "prototype_sha256": prototype.get("prototype_sha256") or prototype.get("vector_sha256") or proto_sha,
        "metadata": json.dumps(
            {
                "model_id": model_output.get("model_id"),
                "query_vector_sha256": query_sha,
                "prototype_vector_sha256": proto_sha,
                "prototype_embedding_dim": len(prototype_vector),
                "query_embedding_dim": len(query_vector),
                "metric": "cosine_similarity",
                "semantic_fallback_used": False,
                "llm_fallback_used": False,
                "filename_fallback_used": False,
                "metadata_fallback_used": False,
            }
        ),
    }


async def persist_prototype_match(
    db: AsyncSession,
    params: dict[str, Any],
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO sine.prototype_match (
                    analysis_run_id, blob_id, model_output_id, prototype_id,
                    label, category, source, source_uri, license, score,
                    distance, segment_start_sec, segment_end_sec, vector_sha256,
                    prototype_sha256, metadata
                ) VALUES (
                    :analysis_run_id, :blob_id, :model_output_id, :prototype_id,
                    :label, :category, :source, :source_uri, :license, :score,
                    :distance, :segment_start_sec, :segment_end_sec,
                    :vector_sha256, :prototype_sha256, CAST(:metadata AS jsonb)
                )
                RETURNING id::text
                """
            ),
            params,
        )
    ).mappings().first()
    output = dict(params)
    output["id"] = row["id"] if row else None
    output["segment_start"] = params.get("segment_start_sec")
    output["segment_end"] = params.get("segment_end_sec")
    output["metadata"] = json.loads(str(params["metadata"]))
    return output


async def run_and_persist_prototype_matches(
    db: AsyncSession,
    *,
    analysis_run_id: UUID,
    blob_id: UUID,
    model_output: dict[str, Any],
    query_vector: list[float],
    max_matches: int = 5,
    min_score: float = 0.7,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "prototype_matches": [],
        "blocking_reasons": [],
    }
    if not query_vector:
        result["blocking_reasons"].append("query_embedding_missing")
        return result
    if not await registry_relation_exists(db, "sine.prototype_match"):
        result["blocking_reasons"].append("prototype_match_table_missing")
        return result

    prototypes = await select_candidate_prototypes(
        db,
        model_id=str(model_output.get("model_id") or ""),
        limit=500,
    )
    if not prototypes:
        result["blocking_reasons"].append("prototype_catalog_empty")
        return result

    scored: list[tuple[float, dict[str, Any], list[float]]] = []
    for prototype in prototypes:
        vector = _prototype_vector(prototype)
        score = cosine_similarity(query_vector, vector)
        if score is None:
            continue
        if score >= min_score:
            scored.append((score, prototype, vector))
    scored.sort(key=lambda item: item[0], reverse=True)

    for score, prototype, vector in scored[: max(1, int(max_matches or 1))]:
        params = build_prototype_match_insert_params(
            analysis_run_id=analysis_run_id,
            blob_id=blob_id,
            model_output=model_output,
            prototype=prototype,
            query_vector=query_vector,
            prototype_vector=vector,
            score=score,
        )
        if not params:
            continue
        result["prototype_matches"].append(await persist_prototype_match(db, params))

    if not result["prototype_matches"]:
        result["blocking_reasons"].append("no_prototype_match_above_threshold")
    return result
