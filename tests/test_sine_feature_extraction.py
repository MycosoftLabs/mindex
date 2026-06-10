from __future__ import annotations

import numpy as np

from mindex_api.services.sine_acoustic.features import (
    extract_sine_feature_tensor,
    feature_sha256,
    fixed_length_samples,
    iter_audio_windows,
    log_mel_spectrogram,
    mel_filterbank,
)


def _tone(freq: float = 440.0, sr: int = 16000, seconds: float = 1.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_fixed_length_samples_center_pads_short_audio() -> None:
    samples = np.ones(4, dtype=np.float32)
    fixed, padded = fixed_length_samples(samples, 10)

    assert padded is True
    assert fixed.shape == (10,)
    assert np.allclose(fixed[3:7], samples)
    assert fixed[:3].sum() == 0
    assert fixed[7:].sum() == 0


def test_iter_audio_windows_splits_long_audio_without_labels() -> None:
    sr = 10
    samples = np.arange(35, dtype=np.float32)
    windows = list(iter_audio_windows(samples, sr, window_sec=1.0, hop_sec=1.0, pad_final=True))

    assert [window.index for window in windows] == [0, 1, 2, 3]
    assert [round(window.start_sec, 2) for window in windows] == [0.0, 1.0, 2.0, 3.0]
    assert windows[-1].padded is True
    assert all(window.samples.shape == (10,) for window in windows)
    assert not hasattr(windows[0], "label")


def test_log_mel_spectrogram_is_shape_stable_and_positive_bank() -> None:
    sr = 16000
    samples = _tone(sr=sr, seconds=1.0)
    features, metadata = log_mel_spectrogram(samples, sr, n_fft=512, hop_length=128, n_mels=40)
    filters = mel_filterbank(sr, 512, n_mels=40)

    assert features.shape[0] == 40
    assert features.shape[1] > 0
    assert filters.shape == (40, 257)
    assert np.count_nonzero(filters) > 0
    assert metadata["feature_kind"] == "log_mel_spectrogram"
    assert "label" not in metadata


def test_extract_sine_feature_tensor_is_deterministic() -> None:
    sr = 16000
    samples = _tone(sr=sr, seconds=0.5)
    first = extract_sine_feature_tensor(
        samples,
        sr,
        window_sec=1.0,
        n_fft=512,
        hop_length=128,
        n_mels=32,
        max_frames=64,
    )
    second = extract_sine_feature_tensor(
        samples,
        sr,
        window_sec=1.0,
        n_fft=512,
        hop_length=128,
        n_mels=32,
        max_frames=64,
    )

    assert first["tensor"].shape == (1, 1, 32, 64)
    assert first["metadata"]["semantic_free"] is True
    assert first["metadata"]["audio_padded"] is True
    assert first["metadata"]["feature_sha256"] == second["metadata"]["feature_sha256"]
    assert first["metadata"]["feature_sha256"] == feature_sha256(first["features"])
