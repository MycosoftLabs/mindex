"""Canonical SINE detector registry (upstream projects + MINDEX ids)."""

from __future__ import annotations

from typing import Any

DETECTORS: dict[str, dict[str, Any]] = {
    "frequency_fft": {
        "id": "frequency_fft",
        "name": "Fundamental frequency (FFT peak)",
        "category": "acoustic",
        "upstream_project": "arduino-audio-tools",
        "upstream_url": "https://github.com/pschatzmann/arduino-audio-tools/wiki/Simple-Frequency-Detection",
        "description": "Estimates dominant frequency via windowed FFT peak (server-side port of simple frequency detection).",
        "method": "fft_peak",
    },
    "activity_auditok": {
        "id": "activity_auditok",
        "name": "Acoustic activity segmentation",
        "category": "acoustic",
        "upstream_project": "auditok",
        "upstream_url": "https://github.com/amsehili/auditok",
        "description": "Detects speech/activity regions using energy-based segmentation (Auditok).",
        "method": "auditok_split",
    },
    "bird_microsoft": {
        "id": "bird_microsoft",
        "name": "Bird acoustic classifier",
        "category": "acoustic",
        "upstream_project": "microsoft/acoustic-bird-detection",
        "upstream_url": "https://github.com/microsoft/acoustic-bird-detection",
        "description": "Bird vs non-bird scoring using mel-band features and harmonic structure heuristics; upgrade path to published ONNX weights.",
        "method": "mel_harmonic_bird_score",
    },
    "uav_rotor": {
        "id": "uav_rotor",
        "name": "UAV / rotor harmonic detector",
        "category": "acoustic",
        "upstream_project": "pcasabianca/Acoustic-UAV-Identification",
        "upstream_url": "https://github.com/pcasabianca/Acoustic-UAV-Identification",
        "description": "Detects narrowband harmonic stacks typical of multi-rotor UAVs in 80–600 Hz bands.",
        "method": "harmonic_stack_uav",
    },
    "nps_discovery_match": {
        "id": "nps_discovery_match",
        "name": "NPS Acoustic Discovery library match",
        "category": "acoustic",
        "upstream_project": "nationalparkservice/acoustic_discovery",
        "upstream_url": "https://github.com/nationalparkservice/acoustic_discovery",
        "description": "Cross-checks clip features against NPS acoustic discovery taxa/event profiles in MINDEX library.",
        "method": "library_profile_match",
    },
    "deep_signal_features": {
        "id": "deep_signal_features",
        "name": "Deep-Signal feature extraction",
        "category": "acoustic",
        "upstream_project": "dimastatz/deep-signal",
        "upstream_url": "https://github.com/dimastatz/deep-signal",
        "description": "Batch-oriented deep learning feature vector for pattern matching (single-file numpy feature mode).",
        "method": "spectral_embedding",
    },
    "visualisation_sonic": {
        "id": "visualisation_sonic",
        "name": "Sonic Visualiser-style layers",
        "category": "acoustic",
        "upstream_project": "sonic-visualiser",
        "upstream_url": "https://www.sonicvisualiser.org/",
        "description": "Waveform envelope + STFT spectrogram layers for interactive display (Sonic Visualiser–compatible data).",
        "method": "waveform_spectrogram",
    },
}

DEFAULT_DETECTOR_IDS = list(DETECTORS.keys())


def seed_detectors_sql() -> list[tuple]:
    rows = []
    for d in DETECTORS.values():
        rows.append(
            (
                d["id"],
                d["name"],
                d["category"],
                d.get("upstream_project"),
                d.get("upstream_url"),
                d["description"],
                d["method"],
            )
        )
    return rows
