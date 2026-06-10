from __future__ import annotations

from uuid import UUID

from mindex_api.routers.library import _attach_latest_persisted_classification


BLOB_ID = UUID("19689d01-5fb3-4804-b4b7-dcd298737c8d")


def test_library_classify_attaches_latest_persisted_evidence_without_claiming_new_inference() -> None:
    payload = {
        "model_status": "model_unavailable",
        "diagnostics": {"blocking_reasons": ["no_loaded_model"]},
    }
    latest = {
        "analysis_run_id": "analysis-1",
        "model_outputs": [
            {
                "id": "model-output-1",
                "model_id": "sine-esc50-cnn-p0-v1",
                "top_label": "thunderstorm",
                "confidence": 0.82,
                "artifact_sha256": "abc123",
                "label_map_sha256": "def456",
            }
        ],
        "prototype_matches": [],
        "fusion_evidence": [],
        "sound_transcripts": [],
        "identification_summary": {"top_label": "thunderstorm", "status": "model_evidence"},
        "identification_status": "model_evidence",
    }

    result = _attach_latest_persisted_classification(payload, latest, blob_id=BLOB_ID)

    assert result["latest_analysis_run_id"] == "analysis-1"
    assert result["model_outputs"] == latest["model_outputs"]
    assert result["identification_summary"] == latest["identification_summary"]
    assert result["latest_persisted_evidence"]["analysis_run_id"] == "analysis-1"
    assert result["diagnostics"]["new_model_inference_run"] is False
    assert result["diagnostics"]["model_inference_route"] == f"/api/mindex/sine/blobs/{BLOB_ID}/analyze"
    assert result["diagnostics"]["classify_route_mode"] == "detector_view_with_latest_persisted_evidence"


def test_library_classify_reports_missing_persisted_evidence() -> None:
    payload = {"diagnostics": {"blocking_reasons": []}}

    result = _attach_latest_persisted_classification(payload, None, blob_id=BLOB_ID)

    assert result["diagnostics"]["new_model_inference_run"] is False
    assert "no_persisted_analysis_evidence" in result["diagnostics"]["blocking_reasons"]
