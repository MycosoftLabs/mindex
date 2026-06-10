from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_sine_real_ai_e2e.py"
spec = importlib.util.spec_from_file_location("verify_sine_real_ai_e2e", SCRIPT_PATH)
assert spec and spec.loader
e2e = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = e2e
spec.loader.exec_module(e2e)


def _real_analysis_payload() -> dict[str, object]:
    return {
        "model_status": "model_ready",
        "model_outputs": [
            {
                "model_id": "sine-esc50-cnn-p0-v1",
                "artifact_sha256": "artifact123",
                "label_map_sha256": "labels456",
                "top_label": "rain",
                "confidence": 0.92,
            }
        ],
        "prototype_matches": [
            {
                "prototype_id": "sine-esc50-cnn-p0-v1--rain",
                "label": "rain",
                "score": 0.95,
                "vector_sha256": "vector123",
                "prototype_sha256": "proto123",
            }
        ],
        "fusion_evidence": [
            {
                "model_output_id": "model-output-1",
                "kind": "model_label",
                "label": "rain",
                "score": 0.92,
            }
        ],
        "sound_transcripts": [
            {
                "start_sec": 0.0,
                "end_sec": 5.0,
                "label": "rain",
                "model_output_ids": ["model-output-1"],
            }
        ],
    }


def test_validate_analysis_accepts_full_real_evidence() -> None:
    assert e2e.validate_analysis(_real_analysis_payload()) == []


def test_validate_analysis_rejects_detector_only_payload() -> None:
    failures = e2e.validate_analysis(
        {
            "model_status": "model_unavailable",
            "frequency_detections": [{"label": "peak_440hz"}],
            "identification_summary": None,
        }
    )

    names = {failure["name"] for failure in failures}
    assert "analysis.model_status" in names
    assert "analysis.model_outputs" in names
    assert "analysis.prototype_matches" in names
    assert "analysis.fusion_evidence" in names
    assert "analysis.sound_transcripts" in names


def test_validate_models_requires_loaded_checksum_backed_model() -> None:
    assert e2e.validate_models(
        {
            "models": [
                {
                    "model_id": "sine-esc50-cnn-p0-v1",
                    "loaded": True,
                    "artifact_sha256": "artifact123",
                    "label_map_sha256": "labels456",
                }
            ]
        }
    ) == []

    failures = e2e.validate_models({"models": [{"model_id": "sine-esc50-cnn-p0-v1", "loaded": False}]})
    assert failures[0]["name"] == "model.loaded"


def test_validate_prototypes_requires_checksum_backed_rows() -> None:
    assert e2e.validate_prototypes(
        {
            "prototypes": [
                {
                    "prototype_id": "sine-esc50-cnn-p0-v1--rain",
                    "label": "rain",
                    "model_id": "sine-esc50-cnn-p0-v1",
                    "embedding_dim": 128,
                    "vector_sha256": "vector123",
                }
            ]
        }
    ) == []

    failures = e2e.validate_prototypes({"prototypes": [{"prototype_id": "proto-rain", "label": "rain"}]})
    assert failures[0]["name"] == "prototype.catalog_rows"


def test_validate_analysis_requires_scored_prototype_match_not_just_catalog_row() -> None:
    payload = _real_analysis_payload()
    payload["prototype_matches"] = [
        {
            "prototype_id": "sine-esc50-cnn-p0-v1--rain",
            "label": "rain",
            "model_id": "sine-esc50-cnn-p0-v1",
            "embedding_dim": 128,
            "vector_sha256": "vector123",
        }
    ]

    failures = e2e.validate_analysis(payload)
    names = {failure["name"] for failure in failures}
    assert "analysis.prototype_matches" in names


def test_validate_cross_evidence_links_requires_registry_and_catalog_links() -> None:
    models = {
        "models": [
            {
                "model_id": "sine-esc50-cnn-p0-v1",
                "loaded": True,
                "artifact_sha256": "artifact123",
                "label_map_sha256": "labels456",
            }
        ]
    }
    prototypes = {
        "prototypes": [
            {
                "prototype_id": "sine-esc50-cnn-p0-v1--rain",
                "label": "rain",
                "model_id": "sine-esc50-cnn-p0-v1",
                "embedding_dim": 128,
                "vector_sha256": "vector123",
            }
        ]
    }

    assert e2e.validate_cross_evidence_links(models, prototypes, _real_analysis_payload()) == []

    bad_model = _real_analysis_payload()
    bad_model["model_outputs"][0]["artifact_sha256"] = "other-artifact"
    model_failures = e2e.validate_cross_evidence_links(models, prototypes, bad_model)
    assert {failure["name"] for failure in model_failures} == {"analysis.model_registry_link"}

    bad_proto = _real_analysis_payload()
    bad_proto["prototype_matches"][0]["prototype_id"] = "unregistered-prototype"
    proto_failures = e2e.validate_cross_evidence_links(models, prototypes, bad_proto)
    assert {failure["name"] for failure in proto_failures} == {"analysis.prototype_catalog_link"}


def test_run_e2e_fails_without_evidence() -> None:
    class Client:
        def request(self, method: str, path: str, body=None):
            if path.startswith("/api/mindex/sine/models"):
                return {"models": []}
            if path.startswith("/api/mindex/sine/prototypes"):
                return {"prototypes": []}
            if path.startswith("/api/mindex/library/blobs"):
                return {"items": [{"id": "19689d01-5fb3-4804-b4b7-dcd298737c8d"}]}
            if path.startswith("/api/mindex/sine/blobs/"):
                return {"model_status": "model_unavailable", "model_outputs": []}
            return {"status": "ok"}

    report = e2e.run_e2e(Client())

    assert report["status"] == "not_ready"
    names = {check["name"] for check in report["checks"]}
    assert "model.loaded" in names
    assert "prototype.catalog_rows" in names
    assert "analysis.model_outputs" in names


def test_run_e2e_passes_with_full_evidence() -> None:
    class Client:
        def request(self, method: str, path: str, body=None):
            if path.startswith("/api/mindex/sine/models"):
                return {
                    "models": [
                        {
                            "model_id": "sine-esc50-cnn-p0-v1",
                            "loaded": True,
                            "artifact_sha256": "artifact123",
                            "label_map_sha256": "labels456",
                        }
                    ]
                }
            if path.startswith("/api/mindex/sine/prototypes"):
                return {
                    "prototypes": [
                        {
                            "prototype_id": "sine-esc50-cnn-p0-v1--rain",
                            "label": "rain",
                            "model_id": "sine-esc50-cnn-p0-v1",
                            "embedding_dim": 128,
                            "vector_sha256": "vector123",
                        }
                    ]
                }
            if path.startswith("/api/mindex/library/blobs"):
                return {"items": [{"id": "19689d01-5fb3-4804-b4b7-dcd298737c8d"}]}
            if path.startswith("/api/mindex/sine/blobs/"):
                return _real_analysis_payload()
            return {"status": "ok"}

    report = e2e.run_e2e(Client())

    assert report["status"] == "ready"
    assert report["checks"] == []
