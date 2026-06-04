from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from .activity import detect_activity_regions
from .audio_io import load_mono
from .bird import score_bird_presence
from .deep_signal import extract_pattern_embedding
from .detector_registry import DEFAULT_DETECTOR_IDS, DETECTORS
from .frequency import detect_frequency_peaks
from .nps_match import match_nps_style_profiles
from .uav import detect_uav_harmonics
from .visualisation import build_visualisation_layers

logger = logging.getLogger(__name__)


def run_full_analysis(
    wav_path: Path,
    *,
    detectors: Optional[list[str]] = None,
    library_label: Optional[str] = None,
) -> dict[str, Any]:
    """Run all requested detectors; return events + visualisation + per-detector status."""
    requested = detectors or DEFAULT_DETECTOR_IDS
    samples, sr = load_mono(wav_path)
    events: list[dict[str, Any]] = []
    status: dict[str, str] = {}
    visualisation: Optional[dict[str, Any]] = None

    for det_id in requested:
        if det_id not in DETECTORS:
            status[det_id] = "unknown_detector"
            continue
        try:
            if det_id == "frequency_fft":
                for ev in detect_frequency_peaks(samples, sr):
                    ev["detector_id"] = det_id
                    events.append(ev)
            elif det_id == "activity_auditok":
                for ev in detect_activity_regions(wav_path):
                    ev["detector_id"] = det_id
                    events.append(ev)
            elif det_id == "bird_microsoft":
                for ev in score_bird_presence(samples, sr):
                    ev["detector_id"] = det_id
                    events.append(ev)
            elif det_id == "uav_rotor":
                for ev in detect_uav_harmonics(samples, sr):
                    ev["detector_id"] = det_id
                    events.append(ev)
            elif det_id == "nps_discovery_match":
                for ev in match_nps_style_profiles(samples, sr, library_label):
                    ev["detector_id"] = det_id
                    events.append(ev)
            elif det_id == "deep_signal_features":
                for ev in extract_pattern_embedding(samples, sr):
                    ev["detector_id"] = det_id
                    events.append(ev)
            elif det_id == "visualisation_sonic":
                visualisation = build_visualisation_layers(samples, sr)
            status[det_id] = "ok"
        except Exception as exc:
            logger.warning("Detector %s failed: %s", det_id, exc)
            status[det_id] = f"error: {exc}"

    return {
        "events": events,
        "visualisation": visualisation,
        "detector_status": status,
        "sample_rate_hz": sr,
        "duration_sec": len(samples) / max(sr, 1),
    }
