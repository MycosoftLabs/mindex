"""
MINDEX acoustic classifier — SINE detector pipeline only (no chemistry / NLM chemistry).

Runs registered acoustic detectors on a WAV path and returns a Library-ready classification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .event_views import build_library_classification_payload
from .pipeline import run_full_analysis


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return None


def _intish(value: Any, *, minimum: int, maximum: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))


def _floatish(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _visualisation_options_from_contract(contract: dict[str, Any] | None) -> dict[str, Any]:
    """Map Website visualisation quality contract into pipeline options."""
    if not contract:
        return {}
    quality = contract.get("visualisation_quality")
    if not isinstance(quality, dict):
        return {}
    options: dict[str, Any] = {}
    int_map = {
        "waveform_points": ("waveform_points", 128, 65536),
        "max_waveform_points": ("waveform_points", 128, 65536),
        "spec_time_bins": ("spec_time_bins", 16, 4096),
        "max_time_frames": ("spec_time_bins", 16, 4096),
        "spec_freq_bins": ("spec_freq_bins", 16, 2048),
        "max_frequency_bins": ("spec_freq_bins", 16, 2048),
        "fft_size": ("fft_size", 64, 16384),
        "n_fft": ("fft_size", 64, 16384),
        "hop_length": ("hop_length", 16, 8192),
    }
    for source, (target, minimum, maximum) in int_map.items():
        if source in quality:
            parsed = _intish(quality.get(source), minimum=minimum, maximum=maximum)
            if parsed is not None:
                options[target] = parsed
    for source in ("start_sec", "end_sec", "db_floor", "db_ceiling"):
        if source in quality:
            parsed = _floatish(quality.get(source))
            if parsed is not None:
                options[source] = parsed
    if isinstance(quality.get("window_function"), str) and quality["window_function"].strip():
        options["window_function"] = quality["window_function"].strip()
    if isinstance(quality.get("quality"), str) and quality["quality"].strip():
        options["quality"] = quality["quality"].strip()
    elif options:
        options["quality"] = "oscilloscope"
    include_peaks = _boolish(quality.get("include_peaks"))
    if include_peaks is not None:
        options["include_peaks"] = include_peaks
    elif options:
        options["include_peaks"] = True
    return options


def classify_acoustic_file(
    wav_path: Path,
    *,
    detectors: Optional[list[str]] = None,
    library_label: Optional[str] = None,
    request_contract: Optional[dict[str, Any]] = None,
    model_context: Optional[dict[str, Any]] = None,
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
        visualisation_options=_visualisation_options_from_contract(request_contract),
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
        request_contract=request_contract,
        model_context=model_context,
    )
    payload["events"] = events
    payload["detector_status"] = summary["detector_status"]
    payload["duration_sec"] = summary["duration_sec"]
    payload["sample_rate_hz"] = summary["sample_rate_hz"]
    payload["visualisation"] = pipeline.get("visualisation")
    return payload
