"""
MINDEX acoustic classifier — SINE detector pipeline only (no chemistry / NLM chemistry).

Runs registered acoustic detectors on a WAV path and returns a Library-ready classification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .event_views import build_library_classification_payload
from .pipeline import run_full_analysis


def classify_acoustic_file(
    wav_path: Path,
    *,
    detectors: Optional[list[str]] = None,
    library_label: Optional[str] = None,
) -> dict[str, Any]:
    """
    Run SINE acoustic detectors and return grouped classification for MINDEX Library.

    This is the canonical server-side acoustic classifier entry point until
    dedicated ONNX/NLM model weights are mounted on NAS.
    """
    pipeline = run_full_analysis(
        wav_path,
        detectors=detectors,
        library_label=library_label,
    )
    events = pipeline.get("events") or []
    summary = {
        "detector_status": pipeline.get("detector_status") or {},
        "event_count": len(events),
        "duration_sec": pipeline.get("duration_sec"),
        "sample_rate_hz": pipeline.get("sample_rate_hz"),
    }
    payload = build_library_classification_payload(
        events,
        summary=summary,
        visualisation=pipeline.get("visualisation"),
    )
    payload["events"] = events
    payload["detector_status"] = summary["detector_status"]
    payload["duration_sec"] = summary["duration_sec"]
    payload["sample_rate_hz"] = summary["sample_rate_hz"]
    payload["visualisation"] = pipeline.get("visualisation")
    return payload
