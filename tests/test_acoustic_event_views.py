"""Acoustic classifier view mapping tests."""
from __future__ import annotations

from mindex_api.services.sine_acoustic.event_views import (
    build_identification_summary,
    build_library_classification_payload,
    group_events_for_library,
)


def test_group_events_for_library_splits_detectors() -> None:
    events = [
        {
            "detector_id": "frequency_fft",
            "label": "peak_440hz",
            "confidence": 0.9,
            "start_sec": 0.0,
            "end_sec": 0.1,
            "frequency_hz": 440.0,
        },
        {
            "detector_id": "bird_microsoft",
            "label": "bird_likely",
            "confidence": 0.6,
            "start_sec": 0.0,
            "end_sec": 2.0,
        },
    ]
    grouped = group_events_for_library(events)
    assert len(grouped["frequency_detections"]) == 1
    assert len(grouped["bird_detections"]) == 1
    assert grouped["frequency_detections"][0]["start_seconds"] == 0.0
    assert grouped["deep_signal_matches"] == []


def test_build_identification_summary_does_not_promote_detector_labels() -> None:
    grouped = {
        "frequency_detections": [],
        "activity_segments": [],
        "bird_detections": [{"label": "bird_likely", "confidence": 0.72}],
        "uav_detections": [],
        "nps_detections": [],
        "deep_signal_matches": [],
    }
    summary = build_identification_summary(grouped)
    assert summary is None


def test_build_library_classification_payload_keys() -> None:
    request_contract = {
        "status": "provided",
        "requires_registered_model": True,
        "allows_detector_only": True,
    }
    model_context = {
        "model_status": "model_unavailable",
        "model_ready": False,
        "model_registry_ready": False,
        "prototype_catalog_ready": False,
        "runtime_backends": {"torch": False, "onnxruntime": False},
        "runtime_supported": False,
        "inference_ready": False,
        "blocking_reasons": ["model_registry_missing"],
    }
    payload = build_library_classification_payload(
        [
            {"detector_id": "uav_rotor", "label": "uav_harmonic", "confidence": 0.5},
            {"detector_id": "deep_signal_features", "label": "spectral_embedding", "confidence": 0.8},
        ],
        request_contract=request_contract,
        model_context=model_context,
    )
    assert payload["analysis_engine"] == "sine_acoustic"
    assert "uav_detections" in payload
    assert payload["model_status"] == "model_unavailable"
    assert payload["identification_status"] == "detector_only"
    assert payload["identification_summary"] is None
    assert payload["model_outputs"] == []
    assert payload["deep_signal_matches"] == []
    assert payload["deep_signal_detections"][0]["label"] == "spectral_embedding"
    assert payload["fusion_evidence"] == []
    assert payload["sound_transcripts"] == []
    assert payload["request_contract"] == request_contract
    assert payload["diagnostics"]["request_contract"] == request_contract
    assert payload["diagnostics"]["model_ready"] is False
    assert payload["model_context"] == model_context
    assert payload["diagnostics"]["blocking_reasons"] == ["model_registry_missing"]


def test_build_library_classification_payload_uses_model_output_proof() -> None:
    model_output = {
        "id": "model-output-1",
        "model_id": "sine-esc50-resnetish-v1",
        "model_name": "SINE ESC-50 ResNetish",
        "model_version": "1.0.0",
        "framework": "pytorch",
        "runtime": "torchscript",
        "artifact_sha256": "abc123",
        "label_map_sha256": "def456",
        "top_label": "lightning_thunder",
        "confidence": 0.91,
        "window_start_sec": 0.0,
        "window_end_sec": 4.0,
    }
    payload = build_library_classification_payload(
        [{"detector_id": "frequency_fft", "label": "peak_90hz", "confidence": 0.5}],
        model_outputs=[model_output],
    )

    assert payload["model_status"] == "model_ready"
    assert payload["identification_status"] == "model_evidence"
    assert payload["identification_summary"]["top_label"] == "lightning_thunder"
    assert payload["model_outputs"] == [model_output]
    assert payload["diagnostics"]["model_ready"] is True
    assert payload["diagnostics"]["inference_ready"] is True


def test_build_library_classification_payload_rejects_model_output_without_provenance() -> None:
    payload = build_library_classification_payload(
        [],
        model_outputs=[
            {
                "model_id": "sine-esc50-resnetish-v1",
                "top_label": "lightning_thunder",
                "confidence": 0.91,
            }
        ],
    )

    assert payload["model_status"] == "model_unavailable"
    assert payload["identification_status"] == "detector_only"
    assert payload["identification_summary"] is None
    assert payload["diagnostics"]["model_ready"] is False
    assert payload["diagnostics"]["inference_ready"] is False
    assert "unproven_model_or_prototype_evidence" in payload["diagnostics"]["blocking_reasons"]


def test_build_library_classification_payload_rejects_ood_model_output_identity() -> None:
    payload = build_library_classification_payload(
        [],
        model_outputs=[
            {
                "id": "model-output-1",
                "model_id": "sine-esc50-resnetish-v1",
                "top_label": "uav_rotor",
                "confidence": 0.33,
                "ood_score": 0.88,
                "ood_status": "out_of_domain_candidate",
                "artifact_sha256": "abc123",
                "label_map_sha256": "def456",
            }
        ],
    )

    assert payload["identification_status"] == "detector_only"
    assert payload["identification_summary"] is None
    assert payload["model_status"] == "model_unavailable"
    assert "unproven_model_or_prototype_evidence" in payload["diagnostics"]["blocking_reasons"]


def test_build_library_classification_payload_rejects_weak_prototype_match() -> None:
    payload = build_library_classification_payload(
        [],
        prototype_matches=[
            {
                "prototype_id": "proto-lightning-1",
                "label": "lightning_thunder",
                "score": 0.91,
            }
        ],
    )

    assert payload["model_status"] == "model_unavailable"
    assert payload["identification_status"] == "detector_only"
    assert payload["identification_summary"] is None
    assert payload["deep_signal_matches"][0]["label"] == "lightning_thunder"
    assert payload["diagnostics"]["model_ready"] is False
    assert "unproven_model_or_prototype_evidence" in payload["diagnostics"]["blocking_reasons"]


def test_build_library_classification_payload_uses_proven_prototype_match() -> None:
    prototype = {
        "prototype_id": "proto-lightning-1",
        "label": "lightning_thunder",
        "score": 0.91,
        "distance": 0.09,
        "vector_sha256": "abc123",
        "model_id": "sine-esc50-resnetish-v1",
    }
    payload = build_library_classification_payload([], prototype_matches=[prototype])

    assert payload["model_status"] == "model_ready"
    assert payload["identification_status"] == "prototype_evidence"
    assert payload["identification_summary"]["top_label"] == "lightning_thunder"
    assert payload["identification_summary"]["status"] == "prototype_evidence"


def test_build_library_classification_payload_uses_evidence_linked_transcript() -> None:
    transcript = {
        "id": "transcript-1",
        "start_sec": 1.0,
        "end_sec": 2.0,
        "label": "whale_vocalization",
        "confidence": 0.84,
        "model_output_ids": ["model-output-1"],
    }
    payload = build_library_classification_payload([], sound_transcripts=[transcript])

    assert payload["identification_status"] == "transcript_evidence"
    assert payload["identification_summary"]["top_label"] == "whale_vocalization"
    assert payload["sound_transcripts"] == [transcript]
