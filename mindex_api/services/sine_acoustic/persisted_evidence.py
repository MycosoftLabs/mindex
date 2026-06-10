"""Read persisted SINE model/prototype/fusion/transcript evidence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .model_runtime import registry_relation_exists


def _json_value(value: Any) -> Any:
    return value if isinstance(value, (dict, list)) else value


async def _model_outputs(db: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    if not await registry_relation_exists(db, "sine.model_output"):
        return []
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    mo.id::text, mo.analysis_run_id::text, mo.blob_id::text,
                    mo.model_id, ma.model_name, ma.model_version, ma.framework,
                    ma.runtime, ma.artifact_uri, ma.label_map_uri,
                    mo.output_kind, mo.window_start_sec, mo.window_end_sec,
                    mo.top_label, mo.confidence, mo.ood_score, mo.labels,
                    mo.scores, mo.embedding_ref, mo.embedding_sha256,
                    COALESCE(mo.artifact_sha256, ma.artifact_sha256) AS artifact_sha256,
                    COALESCE(mo.label_map_sha256, ma.label_map_sha256) AS label_map_sha256,
                    mo.runtime_ms, mo.latency_ms, ma.training_dataset,
                    ma.metrics_uri, ma.input_sample_rate_hz, ma.window_sec,
                    ma.label_count, ma.embedding_dim, ma.device,
                    ma.backend_commit, mo.metadata, mo.created_at
                FROM sine.model_output mo
                LEFT JOIN sine.model_artifact ma ON ma.model_id = mo.model_id
                WHERE mo.analysis_run_id = :run_id
                ORDER BY mo.window_start_sec NULLS FIRST, mo.confidence DESC NULLS LAST, mo.created_at
                """
            ),
            {"run_id": run_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _prototype_matches(db: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    if not await registry_relation_exists(db, "sine.prototype_match"):
        return []
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    pm.id::text, pm.analysis_run_id::text, pm.blob_id::text,
                    pm.model_output_id::text, pm.prototype_id, pm.label,
                    pm.category, pm.source, pm.source_uri, pm.license,
                    pm.score, pm.distance, pm.segment_start_sec AS segment_start,
                    pm.segment_end_sec AS segment_end, pm.vector_sha256,
                    pm.prototype_sha256, p.model_id, p.embedding_dim,
                    pm.metadata, pm.created_at
                FROM sine.prototype_match pm
                LEFT JOIN sine.prototype p ON p.prototype_id = pm.prototype_id
                WHERE pm.analysis_run_id = :run_id
                ORDER BY pm.segment_start_sec NULLS FIRST, pm.score DESC NULLS LAST, pm.distance ASC NULLS LAST
                """
            ),
            {"run_id": run_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _fusion_evidence(db: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    if not await registry_relation_exists(db, "sine.fusion_evidence"):
        return []
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    id::text, analysis_run_id::text, blob_id::text,
                    detector_event_id::text, model_output_id::text,
                    prototype_match_id::text, kind, label, event_family,
                    event_type, score, weight, detail, evidence, metadata,
                    created_at
                FROM sine.fusion_evidence
                WHERE analysis_run_id = :run_id
                ORDER BY score DESC NULLS LAST, weight DESC NULLS LAST, created_at
                """
            ),
            {"run_id": run_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _sound_transcripts(db: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    if not await registry_relation_exists(db, "sine.sound_transcript"):
        return []
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    id::text, analysis_run_id::text, blob_id::text,
                    start_sec, end_sec, label, description, sound_source,
                    confidence, frequency_range, event_family,
                    model_output_ids::text[] AS model_output_ids,
                    fusion_evidence_ids::text[] AS fusion_evidence_ids,
                    prototype_match_ids::text[] AS prototype_ids,
                    detector_event_ids::text[] AS detector_event_ids,
                    evidence_summary, metadata, created_at
                FROM sine.sound_transcript
                WHERE analysis_run_id = :run_id
                ORDER BY start_sec, end_sec, created_at
                """
            ),
            {"run_id": run_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def list_persisted_analysis_evidence(
    db: AsyncSession,
    run_id: UUID,
) -> dict[str, list[dict[str, Any]]]:
    """Return persisted real AI evidence for an analysis run, if tables exist."""
    model_outputs = await _model_outputs(db, run_id)
    prototype_matches = await _prototype_matches(db, run_id)
    fusion_evidence = await _fusion_evidence(db, run_id)
    sound_transcripts = await _sound_transcripts(db, run_id)
    return {
        "model_outputs": [_json_value(row) for row in model_outputs],
        "prototype_matches": [_json_value(row) for row in prototype_matches],
        "deep_signal_matches": [_json_value(row) for row in prototype_matches],
        "fusion_evidence": [_json_value(row) for row in fusion_evidence],
        "sound_transcripts": [_json_value(row) for row in sound_transcripts],
    }
