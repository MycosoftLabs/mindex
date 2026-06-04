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


def test_build_identification_summary_picks_top_bird() -> None:
    grouped = {
        "frequency_detections": [],
        "activity_segments": [],
        "bird_detections": [{"label": "bird_likely", "confidence": 0.72}],
        "uav_detections": [],
        "nps_detections": [],
        "deep_signal_matches": [],
    }
    summary = build_identification_summary(grouped)
    assert summary["top_label"] == "bird_likely"
    assert summary["confidence"] == 0.72


def test_build_library_classification_payload_keys() -> None:
    payload = build_library_classification_payload(
        [{"detector_id": "uav_rotor", "label": "uav_harmonic", "confidence": 0.5}],
    )
    assert payload["analysis_engine"] == "sine_acoustic"
    assert "uav_detections" in payload
    assert payload["identification_summary"]["status"] in ("classified", "pending")
