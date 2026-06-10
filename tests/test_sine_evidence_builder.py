from __future__ import annotations

import asyncio
import json
from uuid import UUID

from mindex_api.services.sine_acoustic import evidence_builder
from mindex_api.services.sine_acoustic.evidence_builder import (
    build_fusion_evidence_insert_params,
    build_sound_transcript_insert_params,
    persist_evidence_for_model_output,
)


RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
BLOB_ID = UUID("22222222-2222-2222-2222-222222222222")


class _FakeResult:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> dict[str, object] | None:
        return self._row


class _FakeDb:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, object] | None]] = []

    async def execute(self, statement, params: dict[str, object] | None = None) -> _FakeResult:
        sql = str(statement)
        self.commands.append((sql, params))
        if "INSERT INTO sine.fusion_evidence" in sql:
            return _FakeResult({"id": "44444444-4444-4444-4444-444444444444"})
        if "INSERT INTO sine.sound_transcript" in sql:
            return _FakeResult({"id": "55555555-5555-5555-5555-555555555555"})
        return _FakeResult()


def _model_output(**overrides: object) -> dict[str, object]:
    output: dict[str, object] = {
        "id": "33333333-3333-3333-3333-333333333333",
        "analysis_run_id": str(RUN_ID),
        "blob_id": str(BLOB_ID),
        "model_id": "sine-esc50-resnetish-v1",
        "top_label": "lightning_thunder",
        "confidence": 0.91,
        "window_start_sec": 2.0,
        "window_end_sec": 7.0,
        "artifact_sha256": "a" * 64,
        "label_map_sha256": "b" * 64,
        "metadata": {
            "feature_sha256": "c" * 64,
            "runtime": "torchscript",
            "framework": "pytorch",
            "feature_metadata": {"tensor_shape": [1, 1, 64, 100]},
        },
    }
    output.update(overrides)
    return output


def test_build_fusion_evidence_insert_params_requires_model_proof() -> None:
    params = build_fusion_evidence_insert_params(
        analysis_run_id=RUN_ID,
        blob_id=BLOB_ID,
        model_output=_model_output(),
    )

    assert params is not None
    assert params["model_output_id"] == "33333333-3333-3333-3333-333333333333"
    assert params["kind"] == "model_output_identity"
    assert params["label"] == "lightning_thunder"
    assert params["score"] == 0.91
    evidence = json.loads(str(params["evidence"]))
    assert evidence["artifact_sha256"] == "a" * 64
    assert evidence["label_map_sha256"] == "b" * 64
    assert evidence["feature_sha256"] == "c" * 64


def test_build_fusion_evidence_insert_params_rejects_unproven_output() -> None:
    params = build_fusion_evidence_insert_params(
        analysis_run_id=RUN_ID,
        blob_id=BLOB_ID,
        model_output=_model_output(label_map_sha256=""),
    )

    assert params is None


def test_build_sound_transcript_insert_params_links_model_and_fusion_ids() -> None:
    fusion = {
        "id": "44444444-4444-4444-4444-444444444444",
        "event_family": "weather_lightning",
    }
    params = build_sound_transcript_insert_params(
        analysis_run_id=RUN_ID,
        blob_id=BLOB_ID,
        model_output=_model_output(),
        fusion_evidence=fusion,
    )

    assert params is not None
    assert params["label"] == "lightning_thunder"
    assert params["start_sec"] == 2.0
    assert params["end_sec"] == 7.0
    assert params["model_output_ids"] == "{33333333-3333-3333-3333-333333333333}"
    assert params["fusion_evidence_ids"] == "{44444444-4444-4444-4444-444444444444}"
    metadata = json.loads(str(params["metadata"]))
    assert metadata["semantic_fallback_used"] is False
    assert metadata["feature_sha256"] == "c" * 64


def test_persist_evidence_for_model_output_writes_fusion_and_transcript(monkeypatch) -> None:
    async def relation_exists(_db, _relation: str) -> bool:
        return True

    monkeypatch.setattr(evidence_builder, "registry_relation_exists", relation_exists)
    result = asyncio.run(
        persist_evidence_for_model_output(
            _FakeDb(),
            analysis_run_id=RUN_ID,
            blob_id=BLOB_ID,
            model_output=_model_output(),
        )
    )

    assert len(result["fusion_evidence"]) == 1
    assert len(result["sound_transcripts"]) == 1
    assert result["fusion_evidence"][0]["id"] == "44444444-4444-4444-4444-444444444444"
    assert result["sound_transcripts"][0]["model_output_ids"] == [
        "33333333-3333-3333-3333-333333333333"
    ]


def test_persist_evidence_for_model_output_rejects_unproven_output(monkeypatch) -> None:
    async def relation_exists(_db, _relation: str) -> bool:
        return True

    monkeypatch.setattr(evidence_builder, "registry_relation_exists", relation_exists)
    result = asyncio.run(
        persist_evidence_for_model_output(
            _FakeDb(),
            analysis_run_id=RUN_ID,
            blob_id=BLOB_ID,
            model_output=_model_output(artifact_sha256=""),
        )
    )

    assert result["fusion_evidence"] == []
    assert result["sound_transcripts"] == []
    assert result["blocking_reasons"] == ["model_output_not_proven"]
