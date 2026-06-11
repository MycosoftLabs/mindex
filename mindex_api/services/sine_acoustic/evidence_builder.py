"""Build and persist evidence rows derived from proven SINE model outputs.

These helpers intentionally avoid prototype or transcript fabrication. They
only create fusion/transcript rows after a persisted model output has model
identity plus artifact/label-map provenance.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .model_runtime import registry_relation_exists


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _model_output_is_proven(output: dict[str, Any]) -> bool:
    has_id = _has_value(output.get("id"))
    has_model = _has_value(output.get("model_id"))
    has_label = _has_value(output.get("top_label"))
    has_provenance = _has_value(output.get("artifact_sha256")) and _has_value(output.get("label_map_sha256"))
    has_score = output.get("confidence") is not None or output.get("ood_score") is not None
    return has_id and has_model and has_label and has_provenance and has_score


def _json_dump(value: Any, default: Any) -> str:
    if value is None:
        value = default
    return json.dumps(value, default=str)


def _uuid_array_literal(values: list[Any]) -> list[str]:
    # Return a Python list (not a '{...}' string): asyncpg binds a list to a
    # uuid[] column, but rejects the Postgres array-literal string form.
    return [str(value) for value in values if _has_value(value)]


def build_fusion_evidence_insert_params(
    *,
    analysis_run_id: UUID,
    blob_id: UUID,
    model_output: dict[str, Any],
) -> dict[str, Any] | None:
    """Create insert params for a model-backed fusion row."""
    if not _model_output_is_proven(model_output):
        return None
    metadata = model_output.get("metadata") if isinstance(model_output.get("metadata"), dict) else {}
    feature_meta = metadata.get("feature_metadata") if isinstance(metadata.get("feature_metadata"), dict) else {}
    evidence = {
        "model_output_id": model_output.get("id"),
        "model_id": model_output.get("model_id"),
        "artifact_sha256": model_output.get("artifact_sha256"),
        "label_map_sha256": model_output.get("label_map_sha256"),
        "feature_sha256": metadata.get("feature_sha256") or model_output.get("embedding_sha256"),
        "runtime": metadata.get("runtime"),
        "framework": metadata.get("framework"),
        "window_start_sec": model_output.get("window_start_sec"),
        "window_end_sec": model_output.get("window_end_sec"),
        "feature_metadata": feature_meta,
    }
    return {
        "analysis_run_id": analysis_run_id,
        "blob_id": blob_id,
        "detector_event_id": None,
        "model_output_id": model_output["id"],
        "prototype_match_id": None,
        "kind": "model_output_identity",
        "label": model_output.get("top_label"),
        "event_family": metadata.get("event_family"),
        "event_type": model_output.get("top_label"),
        "score": model_output.get("confidence"),
        "weight": 1.0,
        "detail": "Evidence-backed SINE acoustic identity from a verified model output.",
        "evidence": _json_dump(evidence, {}),
        "metadata": _json_dump(
            {
                "source": "sine.model_output",
                "semantic_fallback_used": False,
                "llm_fallback_used": False,
                "filename_fallback_used": False,
                "metadata_fallback_used": False,
            },
            {},
        ),
    }


def build_sound_transcript_insert_params(
    *,
    analysis_run_id: UUID,
    blob_id: UUID,
    model_output: dict[str, Any],
    fusion_evidence: dict[str, Any],
) -> dict[str, Any] | None:
    """Create insert params for a transcript row backed by model/fusion IDs."""
    if not _model_output_is_proven(model_output) or not _has_value(fusion_evidence.get("id")):
        return None
    start = model_output.get("window_start_sec")
    end = model_output.get("window_end_sec")
    if start is None or end is None:
        return None
    label = str(model_output.get("top_label") or "").strip()
    metadata = model_output.get("metadata") if isinstance(model_output.get("metadata"), dict) else {}
    return {
        "analysis_run_id": analysis_run_id,
        "blob_id": blob_id,
        "start_sec": start,
        "end_sec": end,
        "label": label,
        "description": f"SINE model evidence identifies this acoustic window as {label}.",
        "sound_source": None,
        "confidence": model_output.get("confidence"),
        "frequency_range": None,
        "event_family": fusion_evidence.get("event_family"),
        "model_output_ids": _uuid_array_literal([model_output["id"]]),
        "fusion_evidence_ids": _uuid_array_literal([fusion_evidence["id"]]),
        "prototype_match_ids": _uuid_array_literal([]),
        "detector_event_ids": _uuid_array_literal([]),
        "evidence_summary": "Transcript is linked to a verified SINE model output and fusion evidence row.",
        "metadata": _json_dump(
            {
                "model_id": model_output.get("model_id"),
                "artifact_sha256": model_output.get("artifact_sha256"),
                "label_map_sha256": model_output.get("label_map_sha256"),
                "feature_sha256": metadata.get("feature_sha256"),
                "semantic_fallback_used": False,
                "llm_fallback_used": False,
                "filename_fallback_used": False,
                "metadata_fallback_used": False,
            },
            {},
        ),
    }


async def persist_fusion_evidence(
    db: AsyncSession,
    params: dict[str, Any],
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO sine.fusion_evidence (
                    analysis_run_id, blob_id, detector_event_id, model_output_id,
                    prototype_match_id, kind, label, event_family, event_type,
                    score, weight, detail, evidence, metadata
                ) VALUES (
                    :analysis_run_id, :blob_id, :detector_event_id, :model_output_id,
                    :prototype_match_id, :kind, :label, :event_family, :event_type,
                    :score, :weight, :detail, CAST(:evidence AS jsonb), CAST(:metadata AS jsonb)
                )
                RETURNING id::text
                """
            ),
            params,
        )
    ).mappings().first()
    output = dict(params)
    output["id"] = row["id"] if row else None
    output["evidence"] = json.loads(str(params["evidence"]))
    output["metadata"] = json.loads(str(params["metadata"]))
    return output


async def persist_sound_transcript(
    db: AsyncSession,
    params: dict[str, Any],
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO sine.sound_transcript (
                    analysis_run_id, blob_id, start_sec, end_sec, label,
                    description, sound_source, confidence, frequency_range,
                    event_family, model_output_ids, fusion_evidence_ids,
                    prototype_match_ids, detector_event_ids, evidence_summary, metadata
                ) VALUES (
                    :analysis_run_id, :blob_id, :start_sec, :end_sec, :label,
                    :description, :sound_source, :confidence, :frequency_range,
                    :event_family, CAST(:model_output_ids AS uuid[]),
                    CAST(:fusion_evidence_ids AS uuid[]),
                    CAST(:prototype_match_ids AS uuid[]),
                    CAST(:detector_event_ids AS uuid[]),
                    :evidence_summary, CAST(:metadata AS jsonb)
                )
                RETURNING id::text
                """
            ),
            params,
        )
    ).mappings().first()
    output = dict(params)
    output["id"] = row["id"] if row else None
    output["model_output_ids"] = list(params["model_output_ids"])
    output["fusion_evidence_ids"] = list(params["fusion_evidence_ids"])
    output["prototype_ids"] = list(params["prototype_match_ids"])
    output["detector_event_ids"] = list(params["detector_event_ids"])
    output["metadata"] = json.loads(str(params["metadata"]))
    return output


async def persist_evidence_for_model_output(
    db: AsyncSession,
    *,
    analysis_run_id: UUID,
    blob_id: UUID,
    model_output: dict[str, Any],
) -> dict[str, Any]:
    """Persist fusion/transcript rows for one proven model output."""
    result: dict[str, Any] = {
        "fusion_evidence": [],
        "sound_transcripts": [],
        "blocking_reasons": [],
    }
    if not _model_output_is_proven(model_output):
        result["blocking_reasons"].append("model_output_not_proven")
        return result
    if not await registry_relation_exists(db, "sine.fusion_evidence"):
        result["blocking_reasons"].append("fusion_evidence_table_missing")
        return result

    fusion_params = build_fusion_evidence_insert_params(
        analysis_run_id=analysis_run_id,
        blob_id=blob_id,
        model_output=model_output,
    )
    if not fusion_params:
        result["blocking_reasons"].append("fusion_params_not_proven")
        return result
    fusion = await persist_fusion_evidence(db, fusion_params)
    result["fusion_evidence"].append(fusion)

    if not await registry_relation_exists(db, "sine.sound_transcript"):
        result["blocking_reasons"].append("sound_transcript_table_missing")
        return result
    transcript_params = build_sound_transcript_insert_params(
        analysis_run_id=analysis_run_id,
        blob_id=blob_id,
        model_output=model_output,
        fusion_evidence=fusion,
    )
    if not transcript_params:
        result["blocking_reasons"].append("transcript_params_not_proven")
        return result
    transcript = await persist_sound_transcript(db, transcript_params)
    result["sound_transcripts"].append(transcript)
    return result
