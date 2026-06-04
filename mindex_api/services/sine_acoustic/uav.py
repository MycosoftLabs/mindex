"""UAV / rotor harmonic stack detection (Acoustic-UAV-Identification style)."""

from __future__ import annotations

from typing import Any

import numpy as np


def detect_uav_harmonics(samples: np.ndarray, sample_rate: int) -> list[dict[str, Any]]:
    try:
        from scipy import signal
    except ImportError as exc:
        raise RuntimeError("scipy required for UAV detection") from exc

    if len(samples) < sample_rate:
        return []

    f, t, Sxx = signal.spectrogram(samples, fs=sample_rate, nperseg=2048, noverlap=1536)
    band = (f >= 80) & (f <= 600)
    if not band.any():
        return []

    band_power = Sxx[band].mean(axis=0)
    threshold = float(np.percentile(band_power, 92))
    hits = band_power >= threshold
    if not hits.any():
        return [
            {
                "label": "uav_not_detected",
                "confidence": 0.2,
                "start_sec": 0.0,
                "end_sec": len(samples) / sample_rate,
                "frequency_hz": None,
                "metadata": {"method": "harmonic_stack_uav"},
            }
        ]

    peak_f = float(f[band][int(np.argmax(Sxx[band].mean(axis=1)))])
    score = min(1.0, float(band_power.max() / (band_power.mean() + 1e-9)) / 8.0)
    return [
        {
            "label": "uav_rotor_likely" if score >= 0.4 else "uav_rotor_possible",
            "confidence": round(score, 4),
            "start_sec": 0.0,
            "end_sec": len(samples) / sample_rate,
            "frequency_hz": peak_f,
            "metadata": {
                "peak_rotor_hz": peak_f,
                "method": "harmonic_stack_uav",
                "upstream": "pcasabianca/Acoustic-UAV-Identification",
            },
        }
    ]
