"""Reusable SINE acoustic feature extraction for model inference.

These helpers are intentionally semantic-free. They turn real decoded audio
into deterministic windowed tensors that PyTorch, TorchScript, ONNX Runtime, or
prototype-search code can consume later.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np


@dataclass(frozen=True)
class AudioWindow:
    index: int
    start_sec: float
    end_sec: float
    samples: np.ndarray
    padded: bool = False


def _window_values(name: str, size: int) -> np.ndarray:
    normalized = (name or "hann").strip().lower()
    if normalized in {"hann", "hanning"}:
        return np.hanning(size)
    if normalized == "hamming":
        return np.hamming(size)
    if normalized == "blackman":
        return np.blackman(size)
    return np.ones(size)


def fixed_length_samples(
    samples: np.ndarray,
    target_samples: int,
    *,
    mode: str = "center",
) -> tuple[np.ndarray, bool]:
    """Pad or crop audio to the exact sample length required by a model."""
    target = max(1, int(target_samples))
    data = np.asarray(samples, dtype=np.float32)
    if len(data) == target:
        return data.copy(), False
    if len(data) < target:
        padded = np.zeros(target, dtype=np.float32)
        if mode == "left":
            start = 0
        elif mode == "right":
            start = target - len(data)
        else:
            start = (target - len(data)) // 2
        padded[start : start + len(data)] = data
        return padded, True
    if mode == "left":
        start = 0
    elif mode == "right":
        start = len(data) - target
    else:
        start = (len(data) - target) // 2
    return data[start : start + target].copy(), False


def iter_audio_windows(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_sec: float = 30.0,
    hop_sec: float | None = None,
    max_windows: int | None = None,
    pad_final: bool = False,
) -> Iterable[AudioWindow]:
    """Yield bounded analysis windows for short clips and long NAS files."""
    sr = max(1, int(sample_rate or 1))
    data = np.asarray(samples, dtype=np.float32)
    if len(data) == 0:
        return
    window_samples = max(1, int(round(float(window_sec or 0) * sr)))
    hop_samples = max(1, int(round(float(hop_sec if hop_sec is not None else window_sec) * sr)))
    limit = max_windows if max_windows is None else max(0, int(max_windows))
    emitted = 0
    start = 0
    index = 0
    while start < len(data):
        if limit is not None and emitted >= limit:
            break
        end = start + window_samples
        chunk = data[start:end]
        padded = False
        if len(chunk) < window_samples:
            if not pad_final and start > 0:
                break
            chunk, padded = fixed_length_samples(chunk, window_samples, mode="left")
        yield AudioWindow(
            index=index,
            start_sec=start / sr,
            end_sec=min(end, len(data)) / sr,
            samples=chunk,
            padded=padded,
        )
        emitted += 1
        index += 1
        if end >= len(data):
            break
        start += hop_samples


def stft_power(
    samples: np.ndarray,
    sample_rate: int,
    *,
    n_fft: int = 1024,
    hop_length: int = 320,
    window_function: str = "hann",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute a deterministic power spectrogram using NumPy FFT."""
    sr = max(1, int(sample_rate or 1))
    fft_size = max(16, int(n_fft or 1024))
    hop = max(1, int(hop_length or max(1, fft_size // 2)))
    data = np.asarray(samples, dtype=np.float32)
    if len(data) == 0:
        data = np.zeros(fft_size, dtype=np.float32)
    if len(data) < fft_size:
        padded = np.zeros(fft_size, dtype=np.float32)
        padded[: len(data)] = data
        data = padded
    starts = list(range(0, max(1, len(data) - fft_size + 1), hop))
    last_start = max(0, len(data) - fft_size)
    if starts[-1] != last_start:
        starts.append(last_start)
    window = _window_values(window_function, fft_size).astype(np.float32)
    scale = max(float(np.sum(window**2)), 1e-12)
    frames: list[np.ndarray] = []
    for start in starts:
        frame = data[start : start + fft_size]
        if len(frame) < fft_size:
            padded = np.zeros(fft_size, dtype=np.float32)
            padded[: len(frame)] = frame
            frame = padded
        frames.append((np.abs(np.fft.rfft(frame * window)) ** 2 / scale).astype(np.float32))
    freqs = np.fft.rfftfreq(fft_size, 1.0 / sr).astype(np.float32)
    times = ((np.asarray(starts, dtype=np.float32) + fft_size / 2) / sr).astype(np.float32)
    return freqs, times, np.asarray(frames, dtype=np.float32).T


def hz_to_mel(hz: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)


def mel_to_hz(mel: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(mel) / 2595.0) - 1.0)


def mel_filterbank(
    sample_rate: int,
    n_fft: int,
    *,
    n_mels: int = 64,
    f_min: float = 0.0,
    f_max: float | None = None,
) -> np.ndarray:
    """Build a triangular mel filter bank without external dependencies."""
    sr = max(1, int(sample_rate or 1))
    fft_size = max(16, int(n_fft or 1024))
    mel_count = max(1, int(n_mels or 64))
    max_hz = float(f_max if f_max is not None else sr / 2)
    max_hz = min(max_hz, sr / 2)
    min_hz = max(0.0, float(f_min or 0.0))
    mel_points = np.linspace(float(hz_to_mel(min_hz)), float(hz_to_mel(max_hz)), mel_count + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((fft_size + 1) * hz_points / sr).astype(int)
    bins = np.clip(bins, 0, fft_size // 2)
    filters = np.zeros((mel_count, fft_size // 2 + 1), dtype=np.float32)
    for idx in range(mel_count):
        left, center, right = int(bins[idx]), int(bins[idx + 1]), int(bins[idx + 2])
        if center <= left:
            center = min(left + 1, filters.shape[1] - 1)
        if right <= center:
            right = min(center + 1, filters.shape[1])
        if center > left:
            filters[idx, left:center] = (np.arange(left, center) - left) / max(center - left, 1)
        if right > center:
            filters[idx, center:right] = (right - np.arange(center, right)) / max(right - center, 1)
    return filters


def log_mel_spectrogram(
    samples: np.ndarray,
    sample_rate: int,
    *,
    n_fft: int = 1024,
    hop_length: int = 320,
    n_mels: int = 64,
    f_min: float = 0.0,
    f_max: float | None = None,
    window_function: str = "hann",
    log_floor: float = 1e-10,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Return log-mel features with deterministic metadata."""
    freqs, times, power = stft_power(
        samples,
        sample_rate,
        n_fft=n_fft,
        hop_length=hop_length,
        window_function=window_function,
    )
    filters = mel_filterbank(
        sample_rate,
        n_fft,
        n_mels=n_mels,
        f_min=f_min,
        f_max=f_max,
    )
    mel_power = filters @ power
    log_mel = np.log(np.maximum(mel_power, float(log_floor or 1e-10))).astype(np.float32)
    metadata = {
        "feature_kind": "log_mel_spectrogram",
        "sample_rate_hz": int(sample_rate),
        "n_fft": int(n_fft),
        "hop_length": int(hop_length),
        "n_mels": int(n_mels),
        "f_min_hz": float(f_min),
        "f_max_hz": float(f_max if f_max is not None else sample_rate / 2),
        "window_function": window_function,
        "frame_count": int(log_mel.shape[1]),
        "frequency_bin_count": int(power.shape[0]),
        "time_start_sec": float(times[0]) if len(times) else 0.0,
        "time_end_sec": float(times[-1]) if len(times) else 0.0,
        "frequency_min_hz": float(freqs[0]) if len(freqs) else 0.0,
        "frequency_max_hz": float(freqs[-1]) if len(freqs) else float(sample_rate / 2),
    }
    return log_mel, metadata


def feature_sha256(features: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(features.astype(np.float32, copy=False))
    return hashlib.sha256(contiguous.tobytes()).hexdigest()


def extract_sine_feature_tensor(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_sec: float = 30.0,
    n_fft: int = 1024,
    hop_length: int = 320,
    n_mels: int = 64,
    max_frames: int | None = None,
) -> dict[str, Any]:
    """Build the P0 model input tensor for one audio window."""
    target_samples = max(1, int(round(float(window_sec or 0) * max(1, int(sample_rate or 1)))))
    fixed, padded = fixed_length_samples(samples, target_samples, mode="left")
    features, metadata = log_mel_spectrogram(
        fixed,
        sample_rate,
        n_fft=n_fft,
        hop_length=hop_length,
        n_mels=n_mels,
    )
    if max_frames is not None:
        target_frames = max(1, int(max_frames))
        if features.shape[1] < target_frames:
            padded_features = np.zeros((features.shape[0], target_frames), dtype=np.float32)
            padded_features[:, : features.shape[1]] = features
            features = padded_features
            metadata["feature_frame_padded"] = True
        elif features.shape[1] > target_frames:
            features = features[:, :target_frames]
            metadata["feature_frame_clipped"] = True
    tensor = features[np.newaxis, np.newaxis, :, :].astype(np.float32)
    metadata.update(
        {
            "tensor_shape": list(tensor.shape),
            "feature_shape": list(features.shape),
            "audio_padded": padded,
            "feature_sha256": feature_sha256(features),
            "semantic_free": True,
        }
    )
    return {"tensor": tensor, "features": features, "metadata": metadata}
