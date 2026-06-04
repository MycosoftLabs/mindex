"""SINE analysis pipeline smoke tests."""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import numpy as np
import pytest

from mindex_api.services.sine_acoustic.frequency import detect_frequency_peaks
from mindex_api.services.sine_acoustic.pipeline import run_full_analysis


def _write_test_wav(path: Path, freq: float = 440.0, sr: int = 16000, seconds: float = 1.0) -> None:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    samples = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())


@pytest.mark.parametrize("freq", [440.0])
def test_frequency_detection_near_tone(freq: float) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        p = Path(tmp.name)
    try:
        _write_test_wav(p, freq=freq)
        from mindex_api.services.sine_acoustic.audio_io import load_mono

        samples, sr = load_mono(p)
        events = detect_frequency_peaks(samples, sr)
        assert events
        assert any(abs(e["frequency_hz"] - freq) < 80 for e in events)
    finally:
        p.unlink(missing_ok=True)


def test_full_pipeline_runs() -> None:
    pytest.importorskip("scipy")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        p = Path(tmp.name)
    try:
        _write_test_wav(p)
        result = run_full_analysis(
            p,
            detectors=["frequency_fft", "visualisation_sonic", "bird_microsoft"],
        )
        assert result["detector_status"]["frequency_fft"] == "ok"
        assert result["visualisation"] is not None
        assert result["events"]
    finally:
        p.unlink(missing_ok=True)
