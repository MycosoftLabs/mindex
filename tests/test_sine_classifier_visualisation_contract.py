from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import numpy as np

from mindex_api.services.sine_acoustic.classifier import classify_acoustic_file


def _write_test_wav(path: Path, freq: float = 440.0, sr: int = 16000, seconds: float = 2.0) -> None:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    samples = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())


def test_classifier_uses_visualisation_quality_contract() -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        _write_test_wav(path)
        payload = classify_acoustic_file(
            path,
            detectors=["visualisation_sonic"],
            request_contract={
                "visualisation_quality": {
                    "max_waveform_points": 4096,
                    "max_time_frames": 512,
                    "max_frequency_bins": 128,
                    "fft_size": 1024,
                    "hop_length": 128,
                    "window_function": "hann",
                    "quality": "oscilloscope",
                    "include_peaks": True,
                }
            },
        )
        vis = payload["visualisation"]
        assert vis["quality"] == "oscilloscope"
        assert len(vis["waveform"]["times"]) == 4096
        assert len(vis["spectrogram"]["frequencies"]) == 128
        assert len(vis["spectrogram"]["times"]) == 512
        assert vis["fft_size"] == 1024
        assert vis["hop_length"] == 128
        assert vis["peaks"]
        assert payload["request_contract"]["visualisation_quality"]["quality"] == "oscilloscope"
    finally:
        path.unlink(missing_ok=True)


def test_classifier_keeps_standard_visualisation_when_contract_absent() -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        _write_test_wav(path)
        payload = classify_acoustic_file(path, detectors=["visualisation_sonic"])
        vis = payload["visualisation"]
        assert vis["quality"] == "standard"
        assert len(vis["waveform"]["times"]) == 800
        assert len(vis["spectrogram"]["frequencies"]) == 64
        assert len(vis["spectrogram"]["times"]) == 128
    finally:
        path.unlink(missing_ok=True)
