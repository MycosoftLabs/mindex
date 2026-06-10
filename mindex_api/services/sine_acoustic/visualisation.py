"""Waveform + spectrogram layers for Sonic Visualiser–style UI."""

from __future__ import annotations

from typing import Any

import numpy as np


def _window_values(name: str, size: int) -> np.ndarray:
    normalized = (name or "hann").strip().lower()
    if normalized in {"hann", "hanning"}:
        return np.hanning(size)
    if normalized == "hamming":
        return np.hamming(size)
    if normalized == "blackman":
        return np.blackman(size)
    return np.ones(size)


def _numpy_spectrogram(
    samples: np.ndarray,
    *,
    sample_rate: int,
    nperseg: int,
    noverlap: int,
    window_function: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hop = max(1, nperseg - noverlap)
    if len(samples) == 0:
        samples = np.zeros(1, dtype=np.float32)
    if len(samples) < nperseg:
        padded = np.zeros(nperseg, dtype=np.float32)
        padded[: len(samples)] = samples
        samples = padded
    starts = list(range(0, max(1, len(samples) - nperseg + 1), hop))
    last_start = max(0, len(samples) - nperseg)
    if starts[-1] != last_start:
        starts.append(last_start)
    window = _window_values(window_function, nperseg).astype(np.float32)
    scale = max(float(np.sum(window**2)), 1e-12)
    columns = []
    for start in starts:
        frame = samples[start : start + nperseg]
        if len(frame) < nperseg:
            padded = np.zeros(nperseg, dtype=np.float32)
            padded[: len(frame)] = frame
            frame = padded
        spectrum = np.abs(np.fft.rfft(frame * window)) ** 2 / scale
        columns.append(spectrum.astype(np.float32))
    freqs = np.fft.rfftfreq(nperseg, 1.0 / sample_rate)
    times = (np.asarray(starts, dtype=float) + nperseg / 2) / sample_rate
    return freqs, times, np.asarray(columns, dtype=np.float32).T


def build_visualisation_layers(
    samples: np.ndarray,
    sample_rate: int,
    *,
    waveform_points: int = 800,
    spec_time_bins: int = 128,
    spec_freq_bins: int = 64,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    fft_size: int = 2048,
    hop_length: int | None = None,
    window_function: str = "hann",
    db_floor: float = -96.0,
    db_ceiling: float = 0.0,
    include_peaks: bool = False,
    quality: str = "standard",
) -> dict[str, Any]:
    try:
        from scipy import signal
    except ImportError:
        signal = None

    sample_rate = max(int(sample_rate or 0), 1)
    total_duration = len(samples) / sample_rate
    start = max(0.0, float(start_sec or 0.0))
    end = total_duration if end_sec is None else max(start, float(end_sec))
    start = min(start, total_duration)
    end = min(end, total_duration)
    start_idx = int(start * sample_rate)
    end_idx = int(end * sample_rate)
    windowed = samples[start_idx:end_idx]
    duration = len(windowed) / sample_rate
    if len(samples) == 0:
        return {
            "visualisation_status": "ready",
            "duration_sec": 0,
            "sample_rate_hz": sample_rate,
            "channels": 1,
            "waveform": {"times": [], "amplitudes": []},
            "spectrogram": {"times": [], "frequencies": [], "power_db": []},
            "peaks": [],
            "fft_size": fft_size,
            "hop_length": hop_length,
            "window_function": window_function,
            "frequency_min_hz": 0,
            "frequency_max_hz": sample_rate / 2,
            "db_floor": db_floor,
            "db_ceiling": db_ceiling,
            "quality": quality,
            "reference": "https://www.sonicvisualiser.org/",
        }
    if len(windowed) == 0:
        windowed = samples[:0]

    target_waveform_points = max(1, int(waveform_points or 1))
    waveform_count = min(target_waveform_points, max(len(windowed), 1))
    if len(windowed):
        wave_idx = np.linspace(0, len(windowed) - 1, waveform_count, dtype=int)
        decimated = windowed[wave_idx]
        times_w = (start + (wave_idx / sample_rate)).tolist()
        amps = decimated.astype(float).tolist()
    else:
        times_w = []
        amps = []

    requested_fft_size = max(16, int(fft_size or 2048))
    nperseg = min(requested_fft_size, max(16, len(windowed)))
    requested_hop = int(hop_length or max(1, nperseg // 2))
    actual_hop = max(1, min(requested_hop, nperseg))
    noverlap = max(0, nperseg - actual_hop)
    if signal is not None:
        f, t, Sxx = signal.spectrogram(
            windowed if len(windowed) else samples[:1],
            fs=sample_rate,
            window=window_function,
            nperseg=nperseg,
            noverlap=noverlap,
        )
        dsp_backend = "scipy.signal.spectrogram"
    else:
        f, t, Sxx = _numpy_spectrogram(
            windowed if len(windowed) else samples[:1],
            sample_rate=sample_rate,
            nperseg=nperseg,
            noverlap=noverlap,
            window_function=window_function,
        )
        dsp_backend = "numpy.rfft"
    target_time_bins = max(1, int(spec_time_bins or 1))
    target_freq_bins = max(1, int(spec_freq_bins or 1))
    f_idx = np.linspace(0, len(f) - 1, min(target_freq_bins, len(f)), dtype=int)
    selected_freqs = f[f_idx]
    source_power = 10 * np.log10(Sxx[f_idx] + 1e-12)
    source_times = np.asarray(t, dtype=float)
    if source_power.shape[1] == 0:
        target_times = np.asarray([], dtype=float)
        power = source_power
    elif source_power.shape[1] == 1:
        target_times = np.linspace(float(source_times[0]), float(source_times[0]), target_time_bins)
        power = np.repeat(source_power, target_time_bins, axis=1)
    else:
        target_times = np.linspace(float(source_times[0]), float(source_times[-1]), target_time_bins)
        power = np.vstack([np.interp(target_times, source_times, row) for row in source_power])
    raw_min = float(np.nanmin(power)) if power.size else None
    raw_max = float(np.nanmax(power)) if power.size else None
    clipped = np.clip(power, db_floor, db_ceiling) if power.size else power
    peak_rows: list[dict[str, Any]] = []
    if include_peaks and power.size:
        flat = power.ravel()
        peak_count = min(48, flat.size)
        top_idx = np.argpartition(flat, -peak_count)[-peak_count:]
        top_idx = top_idx[np.argsort(flat[top_idx])[::-1]]
        seen: set[tuple[int, int]] = set()
        for idx in top_idx:
            freq_i, time_i = np.unravel_index(int(idx), power.shape)
            key = (freq_i, time_i)
            if key in seen:
                continue
            seen.add(key)
            peak_rows.append(
                {
                    "time_sec": float(start + target_times[time_i]) if len(target_times) else start,
                    "frequency_hz": float(selected_freqs[freq_i]),
                    "magnitude_db": float(power[freq_i, time_i]),
                    "prominence": float(power[freq_i, time_i] - (raw_min or power[freq_i, time_i])),
                    "source": "stft_power",
                }
            )
    return {
        "visualisation_status": "ready",
        "duration_sec": duration,
        "sample_rate_hz": sample_rate,
        "channels": 1,
        "start_sec": start,
        "end_sec": end,
        "waveform": {
            "times": times_w,
            "amplitudes": amps,
        },
        "spectrogram": {
            "times": (start + target_times).tolist() if len(target_times) else [],
            "frequencies": selected_freqs.tolist(),
            "power_db": clipped.tolist(),
        },
        "peaks": peak_rows,
        "fft_size": nperseg,
        "requested_fft_size": requested_fft_size,
        "hop_length": actual_hop,
        "window_function": window_function,
        "frequency_min_hz": float(selected_freqs[0]) if len(selected_freqs) else 0,
        "frequency_max_hz": float(selected_freqs[-1]) if len(selected_freqs) else sample_rate / 2,
        "db_floor": db_floor,
        "db_ceiling": db_ceiling,
        "raw_db_min": raw_min,
        "raw_db_max": raw_max,
        "normalization": "power_db_clipped",
        "dsp_backend": dsp_backend,
        "quality": quality,
        "clamp": {
            "waveform_points_requested": target_waveform_points,
            "waveform_points_returned": len(times_w),
            "time_frames_requested": target_time_bins,
            "time_frames_returned": int(clipped.shape[1]) if clipped.ndim == 2 else 0,
            "frequency_bins_requested": target_freq_bins,
            "frequency_bins_returned": len(selected_freqs),
            "source_time_frames": int(source_power.shape[1]) if source_power.ndim == 2 else 0,
            "time_resampled": bool(source_power.ndim == 2 and source_power.shape[1] not in (0, target_time_bins)),
        },
        "reference": "https://www.sonicvisualiser.org/",
        "layers": ["waveform", "spectrogram"],
    }
