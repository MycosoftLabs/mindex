"""Bird-oriented scoring inspired by microsoft/acoustic-bird-detection feature pipeline."""

from __future__ import annotations

from typing import Any

import numpy as np


def score_bird_presence(samples: np.ndarray, sample_rate: int) -> list[dict[str, Any]]:
    """Heuristic bird score from mel energy + high-frequency chirp ratio."""
    try:
        from scipy import signal
    except ImportError as exc:
        raise RuntimeError("scipy required for bird detection") from exc

    if len(samples) < sample_rate // 2:
        return []

    f, t, Sxx = signal.spectrogram(samples, fs=sample_rate, nperseg=1024, noverlap=768)
    power = Sxx + 1e-12
    mel_bands = (f >= 1000) & (f <= 8000)
    low_bands = (f >= 200) & (f < 2000)
    chirp_ratio = float(power[mel_bands].sum() / (power.sum() + 1e-12))
    low_ratio = float(power[low_bands].sum() / (power.sum() + 1e-12))
    score = min(1.0, chirp_ratio * 1.4 + low_ratio * 0.3)
    label = "bird_likely" if score >= 0.35 else "bird_unlikely"
    return [
        {
            "label": label,
            "confidence": round(score, 4),
            "start_sec": 0.0,
            "end_sec": len(samples) / sample_rate,
            "frequency_hz": None,
            "metadata": {
                "chirp_ratio": round(chirp_ratio, 4),
                "low_ratio": round(low_ratio, 4),
                "method": "mel_harmonic_bird_score",
                "upstream": "microsoft/acoustic-bird-detection",
            },
        }
    ]
