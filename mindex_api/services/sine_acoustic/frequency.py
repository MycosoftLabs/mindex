"""Fundamental frequency via FFT peak (arduino-audio-tools simple detection pattern)."""

from __future__ import annotations

from typing import Any

import numpy as np


def detect_frequency_peaks(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_size: int = 2048,
    hop: int = 512,
    min_hz: float = 50.0,
    max_hz: float = 8000.0,
) -> list[dict[str, Any]]:
    if len(samples) < frame_size:
        return []
    freqs = np.fft.rfftfreq(frame_size, 1.0 / sample_rate)
    events: list[dict[str, Any]] = []
    for start in range(0, len(samples) - frame_size, hop):
        frame = samples[start : start + frame_size]
        window = frame * np.hanning(frame_size)
        mag = np.abs(np.fft.rfft(window))
        band = (freqs >= min_hz) & (freqs <= max_hz)
        if not band.any():
            continue
        idx = int(np.argmax(mag[band]))
        f_hz = float(freqs[band][idx])
        amp = float(mag[band][idx])
        t0 = start / sample_rate
        t1 = (start + frame_size) / sample_rate
        events.append(
            {
                "label": f"peak_{f_hz:.1f}hz",
                "confidence": min(1.0, amp / (np.max(mag[band]) + 1e-9)),
                "start_sec": t0,
                "end_sec": t1,
                "frequency_hz": f_hz,
                "metadata": {"frame_size": frame_size, "method": "fft_peak"},
            }
        )
    # collapse to top 12 peaks by confidence
    events.sort(key=lambda e: e["confidence"], reverse=True)
    return events[:12]
