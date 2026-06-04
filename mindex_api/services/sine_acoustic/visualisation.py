"""Waveform + spectrogram layers for Sonic Visualiser–style UI."""

from __future__ import annotations

from typing import Any

import numpy as np


def build_visualisation_layers(
    samples: np.ndarray,
    sample_rate: int,
    *,
    waveform_points: int = 800,
    spec_time_bins: int = 128,
    spec_freq_bins: int = 64,
) -> dict[str, Any]:
    try:
        from scipy import signal
    except ImportError as exc:
        raise RuntimeError("scipy required for visualisation") from exc

    duration = len(samples) / max(sample_rate, 1)
    if len(samples) == 0:
        return {
            "duration_sec": 0,
            "sample_rate_hz": sample_rate,
            "waveform": {"times": [], "amplitudes": []},
            "spectrogram": {"times": [], "frequencies": [], "power_db": []},
            "reference": "https://www.sonicvisualiser.org/",
        }

    step = max(1, len(samples) // waveform_points)
    decimated = samples[::step]
    times_w = (np.arange(len(decimated)) * step / sample_rate).tolist()
    amps = decimated.tolist()

    f, t, Sxx = signal.spectrogram(
        samples,
        fs=sample_rate,
        nperseg=min(2048, len(samples)),
        noverlap=None,
    )
    # downsample for JSON payload
    t_idx = np.linspace(0, len(t) - 1, min(spec_time_bins, len(t)), dtype=int)
    f_idx = np.linspace(0, len(f) - 1, min(spec_freq_bins, len(f)), dtype=int)
    power = 10 * np.log10(Sxx[f_idx][:, t_idx] + 1e-12)
    return {
        "duration_sec": duration,
        "sample_rate_hz": sample_rate,
        "waveform": {
            "times": times_w,
            "amplitudes": amps,
        },
        "spectrogram": {
            "times": t[t_idx].tolist(),
            "frequencies": f[f_idx].tolist(),
            "power_db": power.tolist(),
        },
        "reference": "https://www.sonicvisualiser.org/",
        "layers": ["waveform", "spectrogram"],
    }
