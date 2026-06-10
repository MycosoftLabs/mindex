from __future__ import annotations

import asyncio
import json
from uuid import UUID

import pytest

from mindex_api.services.sine_acoustic import prototype_search
from mindex_api.services.sine_acoustic.prototype_search import (
    build_prototype_match_insert_params,
    cosine_similarity,
    extract_query_embedding,
    run_and_persist_prototype_matches,
    vector_sha256,
)


RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
BLOB_ID = UUID("22222222-2222-2222-2222-222222222222")


class _FakeResult:
    def __init__(
        self,
        row: dict[str, object] | None = None,
        rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._row = row
        self._rows = rows or []

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> dict[str, object] | None:
        return self._row

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _FakeDb:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, object] | None]] = []

    async def execute(self, statement, params: dict[str, object] | None = None) -> _FakeResult:
        sql = str(statement)
        self.commands.append((sql, params))
        if "INSERT INTO sine.prototype_match" in sql:
            return _FakeResult({"id": "44444444-4444-4444-4444-444444444444"})
        return _FakeResult()


def _model_output() -> dict[str, object]:
    return {
        "id": "33333333-3333-3333-3333-333333333333",
        "model_id": "sine-embed-v1",
        "window_start_sec": 0.0,
        "window_end_sec": 5.0,
    }


def _prototype(**overrides: object) -> dict[str, object]:
    vector = [1.0, 0.0, 0.0]
    row: dict[str, object] = {
        "prototype_id": "proto-lightning-1",
        "label": "lightning_thunder",
        "domain": "acoustic",
        "category": "weather_lightning",
        "source": "human-tagged MINDEX Library",
        "source_uri": "mindex://library/prototypes/proto-lightning-1",
        "license": "internal",
        "model_id": "sine-embed-v1",
        "embedding_dim": 3,
        "vector_sha256": vector_sha256(vector),
        "prototype_sha256": "p" * 64,
        "metadata": {"vector": vector},
    }
    row.update(overrides)
    return row


def test_cosine_similarity_scores_vectors() -> None:
    assert cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
    assert cosine_similarity([1, 0], [1, 0, 0]) is None


def test_extract_query_embedding_accepts_common_shapes() -> None:
    assert extract_query_embedding({"embedding": [1, 2, 3]}) == [1.0, 2.0, 3.0]
    assert extract_query_embedding({"embedding_output": {"vector": [0.1, 0.2]}}) == [0.1, 0.2]
    assert extract_query_embedding({"embedding": ["bad"]}) == []


def test_build_prototype_match_insert_params_contains_vector_proof() -> None:
    params = build_prototype_match_insert_params(
        analysis_run_id=RUN_ID,
        blob_id=BLOB_ID,
        model_output=_model_output(),
        prototype=_prototype(),
        query_vector=[1.0, 0.0, 0.0],
        prototype_vector=[1.0, 0.0, 0.0],
        score=1.0,
    )

    assert params is not None
    assert params["prototype_id"] == "proto-lightning-1"
    assert params["model_output_id"] == "33333333-3333-3333-3333-333333333333"
    assert params["label"] == "lightning_thunder"
    assert params["score"] == pytest.approx(1.0)
    assert params["distance"] == pytest.approx(0.0)
    assert params["vector_sha256"] == vector_sha256([1.0, 0.0, 0.0])
    metadata = json.loads(str(params["metadata"]))
    assert metadata["metric"] == "cosine_similarity"
    assert metadata["query_embedding_dim"] == 3
    assert metadata["semantic_fallback_used"] is False


def test_build_prototype_match_insert_params_rejects_missing_ids() -> None:
    params = build_prototype_match_insert_params(
        analysis_run_id=RUN_ID,
        blob_id=BLOB_ID,
        model_output={"id": "", "model_id": "sine"},
        prototype=_prototype(),
        query_vector=[1.0, 0.0, 0.0],
        prototype_vector=[1.0, 0.0, 0.0],
        score=1.0,
    )

    assert params is None


def test_run_and_persist_prototype_matches_writes_best_match(monkeypatch) -> None:
    async def relation_exists(_db, _relation: str) -> bool:
        return True

    async def select_prototypes(_db, *, model_id: str | None, limit: int = 500):
        assert model_id == "sine-embed-v1"
        return [
            _prototype(),
            _prototype(
                prototype_id="proto-other",
                label="other",
                vector_sha256=vector_sha256([0.0, 1.0, 0.0]),
                metadata={"vector": [0.0, 1.0, 0.0]},
            ),
        ]

    monkeypatch.setattr(prototype_search, "registry_relation_exists", relation_exists)
    monkeypatch.setattr(prototype_search, "select_candidate_prototypes", select_prototypes)

    result = asyncio.run(
        run_and_persist_prototype_matches(
            _FakeDb(),
            analysis_run_id=RUN_ID,
            blob_id=BLOB_ID,
            model_output=_model_output(),
            query_vector=[1.0, 0.0, 0.0],
            min_score=0.7,
        )
    )

    assert len(result["prototype_matches"]) == 1
    assert result["prototype_matches"][0]["prototype_id"] == "proto-lightning-1"
    assert result["prototype_matches"][0]["vector_sha256"] == vector_sha256([1.0, 0.0, 0.0])


def test_run_and_persist_prototype_matches_requires_query_vector(monkeypatch) -> None:
    async def relation_exists(_db, _relation: str) -> bool:
        return True

    monkeypatch.setattr(prototype_search, "registry_relation_exists", relation_exists)
    result = asyncio.run(
        run_and_persist_prototype_matches(
            _FakeDb(),
            analysis_run_id=RUN_ID,
            blob_id=BLOB_ID,
            model_output=_model_output(),
            query_vector=[],
        )
    )

    assert result["prototype_matches"] == []
    assert result["blocking_reasons"] == ["query_embedding_missing"]
