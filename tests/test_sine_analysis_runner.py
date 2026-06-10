from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import numpy as np

from mindex_api.services.sine_acoustic import analysis_runner
from mindex_api.services.sine_acoustic import evidence_builder
from mindex_api.services.sine_acoustic import prototype_search
from mindex_api.services.sine_acoustic.analysis_runner import (
    build_model_output_insert_params,
    run_and_persist_loaded_model_outputs,
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
        if "INSERT INTO sine.model_output" in sql:
            return _FakeResult({"id": "33333333-3333-3333-3333-333333333333"})
        if "INSERT INTO sine.prototype_match" in sql:
            return _FakeResult({"id": "66666666-6666-6666-6666-666666666666"})
        if "INSERT INTO sine.fusion_evidence" in sql:
            return _FakeResult({"id": "44444444-4444-4444-4444-444444444444"})
        if "INSERT INTO sine.sound_transcript" in sql:
            return _FakeResult({"id": "55555555-5555-5555-5555-555555555555"})
        return _FakeResult()


def _inference_result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "status": "model_output_ready",
        "model_status": "model_ready",
        "model_id": "sine-audio-v1",
        "model_name": "SINE Audio Model",
        "model_version": "1.0.0",
        "framework": "pytorch",
        "runtime": "torchscript",
        "output_kind": "classification",
        "top_label": "lightning_thunder",
        "confidence": 0.91,
        "labels": [{"label": "lightning_thunder", "confidence": 0.91}],
        "scores": {"lightning_thunder": 0.91},
        "artifact_uri": "/mnt/nas/mindex/models/acoustic/sine.pt",
        "artifact_sha256": "a" * 64,
        "label_map_uri": "/mnt/nas/mindex/models/acoustic/labels.json",
        "label_map_sha256": "b" * 64,
        "runtime_ms": 12.5,
        "latency_ms": 12.5,
        "feature_sha256": "c" * 64,
        "embedding": [1.0, 0.0, 0.0],
        "feature_metadata": {"semantic_free": True, "tensor_shape": [1, 1, 64, 100]},
        "tensor_shape": [1, 1, 64, 100],
        "sample_rate_hz": 16000,
        "window_sec": 30.0,
    }
    result.update(overrides)
    return result


def test_build_model_output_insert_params_contains_required_proof() -> None:
    params = build_model_output_insert_params(
        analysis_run_id=RUN_ID,
        blob_id=BLOB_ID,
        inference_result=_inference_result(),
        window_start_sec=1.0,
        window_end_sec=3.0,
    )

    assert params is not None
    assert params["model_id"] == "sine-audio-v1"
    assert params["top_label"] == "lightning_thunder"
    assert params["artifact_sha256"] == "a" * 64
    assert params["label_map_sha256"] == "b" * 64
    assert json.loads(str(params["labels"]))[0]["label"] == "lightning_thunder"
    metadata = json.loads(str(params["metadata"]))
    assert metadata["feature_sha256"] == "c" * 64
    assert metadata["feature_metadata"]["semantic_free"] is True


def test_build_model_output_insert_params_rejects_unproven_result() -> None:
    assert (
        build_model_output_insert_params(
            analysis_run_id=RUN_ID,
            blob_id=BLOB_ID,
            inference_result=_inference_result(artifact_sha256=""),
        )
        is None
    )


def test_run_and_persist_loaded_model_outputs_persists_success(monkeypatch, tmp_path: Path) -> None:
    async def relation_exists(_db, _relation: str) -> bool:
        return True

    async def select_models(_db, *, limit: int = 1):
        return [
            {
                "model_id": "sine-audio-v1",
                "input_sample_rate_hz": 16000,
                "runtime": "torchscript",
                "feature_params": {},
            }
        ]

    async def select_prototypes(_db, *, model_id: str | None, limit: int = 500):
        return [
            {
                "prototype_id": "proto-lightning-1",
                "label": "lightning_thunder",
                "domain": "acoustic",
                "category": "weather_lightning",
                "source": "human-tagged MINDEX Library",
                "source_uri": "mindex://library/prototypes/proto-lightning-1",
                "license": "internal",
                "model_id": model_id,
                "embedding_dim": 3,
                "vector_sha256": "proto-vector-sha",
                "prototype_sha256": "p" * 64,
                "metadata": {"vector": [1.0, 0.0, 0.0]},
            }
        ]

    monkeypatch.setattr(analysis_runner, "registry_relation_exists", relation_exists)
    monkeypatch.setattr(evidence_builder, "registry_relation_exists", relation_exists)
    monkeypatch.setattr(prototype_search, "registry_relation_exists", relation_exists)
    monkeypatch.setattr(analysis_runner, "select_loaded_acoustic_models", select_models)
    monkeypatch.setattr(prototype_search, "select_candidate_prototypes", select_prototypes)
    monkeypatch.setattr(
        analysis_runner,
        "load_mono",
        lambda _path, target_sr=16000: (np.zeros(target_sr, dtype=np.float32), target_sr),
    )
    monkeypatch.setattr(analysis_runner, "run_registered_model_inference", lambda *_args, **_kwargs: _inference_result())

    db = _FakeDb()
    result = asyncio.run(
        run_and_persist_loaded_model_outputs(
            db,
            blob_id=BLOB_ID,
            analysis_run_id=RUN_ID,
            wav_path=tmp_path / "clip.wav",
        )
    )

    assert result["status"] == "model_outputs_persisted"
    assert result["model_status"] == "model_ready"
    assert result["model_outputs_persisted"] == 1
    assert len(result["prototype_matches"]) == 1
    assert len(result["fusion_evidence"]) == 1
    assert len(result["sound_transcripts"]) == 1
    insert = next((params for sql, params in db.commands if "INSERT INTO sine.model_output" in sql), None)
    assert insert is not None
    assert insert["blob_id"] == BLOB_ID
    assert insert["analysis_run_id"] == RUN_ID
    assert insert["window_end_sec"] == 1.0


def test_run_and_persist_loaded_model_outputs_reports_runtime_blocker(monkeypatch, tmp_path: Path) -> None:
    async def relation_exists(_db, _relation: str) -> bool:
        return True

    async def select_models(_db, *, limit: int = 1):
        return [{"model_id": "sine-audio-v1", "input_sample_rate_hz": 16000}]

    monkeypatch.setattr(analysis_runner, "registry_relation_exists", relation_exists)
    monkeypatch.setattr(evidence_builder, "registry_relation_exists", relation_exists)
    monkeypatch.setattr(analysis_runner, "select_loaded_acoustic_models", select_models)
    monkeypatch.setattr(
        analysis_runner,
        "load_mono",
        lambda _path, target_sr=16000: (np.zeros(target_sr, dtype=np.float32), target_sr),
    )
    monkeypatch.setattr(
        analysis_runner,
        "run_registered_model_inference",
        lambda *_args, **_kwargs: {
            "ok": False,
            "status": "model_runtime_unavailable",
            "detail": "torch is not installed",
            "model_outputs": [],
        },
    )

    result = asyncio.run(
        run_and_persist_loaded_model_outputs(
            _FakeDb(),
            blob_id=BLOB_ID,
            analysis_run_id=RUN_ID,
            wav_path=tmp_path / "clip.wav",
        )
    )

    assert result["status"] == "model_outputs_unavailable"
    assert result["model_outputs"] == []
    assert "sine-audio-v1:model_runtime_unavailable" in result["blocking_reasons"]
