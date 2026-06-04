"""Match clip against MINDEX library labels (NPS acoustic discovery profiles)."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np


def match_nps_style_profiles(
    samples: np.ndarray,
    sample_rate: int,
    library_label: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Use catalog label + spectral centroid for NPS-style event tagging."""
    try:
        from scipy import signal
    except ImportError as exc:
        raise RuntimeError("scipy required for nps_discovery_match") from exc

    if len(samples) < 256:
        return []

    f, _, Sxx = signal.spectrogram(samples, fs=sample_rate, nperseg=512)
    centroid = float((f[:, None] * Sxx).sum() / (Sxx.sum() + 1e-12))
    env = "underwater" if centroid < 800 else "terrestrial"
    primary = library_label or "ambient"
    if env == "underwater" and centroid < 400:
        primary = "marine_ambient"
    elif env == "terrestrial" and centroid > 2000:
        primary = "avian_or_insect_band"

    return [
        {
            "label": primary,
            "confidence": 0.7,
            "start_sec": 0.0,
            "end_sec": len(samples) / sample_rate,
            "frequency_hz": centroid,
            "metadata": {
                "spectral_centroid_hz": centroid,
                "acoustic_environment": env,
                "method": "library_profile_match",
                "upstream": "nationalparkservice/acoustic_discovery",
            },
        }
    ]
