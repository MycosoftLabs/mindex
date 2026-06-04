"""Single-file spectral embedding (Deep-Signal–compatible feature vector)."""

from __future__ import annotations

from typing import Any

import numpy as np


def extract_pattern_embedding(samples: np.ndarray, sample_rate: int) -> list[dict[str, Any]]:
    try:
        from scipy import signal
    except ImportError as exc:
        raise RuntimeError("scipy required for deep_signal_features") from exc

    mfcc_bands = 20
    f, t, Sxx = signal.spectrogram(samples, fs=sample_rate, nperseg=1024)
    log_power = np.log10(Sxx + 1e-12)
    # mean spectral profile as compact embedding
    embedding = log_power.mean(axis=1)
    if len(embedding) > mfcc_bands:
        idx = np.linspace(0, len(embedding) - 1, mfcc_bands, dtype=int)
        embedding = embedding[idx]
    vec = embedding.astype(float).tolist()
    return [
        {
            "label": "spectral_embedding",
            "confidence": 1.0,
            "start_sec": 0.0,
            "end_sec": len(samples) / max(sample_rate, 1),
            "frequency_hz": None,
            "metadata": {
                "embedding_dim": len(vec),
                "embedding": vec,
                "method": "spectral_embedding",
                "upstream": "dimastatz/deep-signal",
            },
        }
    ]
