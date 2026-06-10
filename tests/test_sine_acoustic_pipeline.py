"""SINE analysis pipeline smoke tests."""
from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import numpy as np
import pytest

from mindex_api.services.sine_acoustic.frequency import detect_frequency_peaks
from mindex_api.services.sine_acoustic.pipeline import run_full_analysis
from mindex_api.services.sine_acoustic.visualisation import build_visualisation_layers


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


def test_full_pipeline_passes_visualisation_options() -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        p = Path(tmp.name)
    try:
        _write_test_wav(p, seconds=2.0)
        result = run_full_analysis(
            p,
            detectors=["visualisation_sonic"],
            visualisation_options={
                "waveform_points": 4096,
                "spec_time_bins": 512,
                "spec_freq_bins": 128,
                "fft_size": 1024,
                "hop_length": 128,
                "quality": "oscilloscope",
                "include_peaks": True,
            },
        )
        vis = result["visualisation"]
        assert vis["quality"] == "oscilloscope"
        assert len(vis["waveform"]["times"]) == 4096
        assert len(vis["spectrogram"]["frequencies"]) == 128
        assert len(vis["spectrogram"]["times"]) == 512
        assert len(vis["spectrogram"]["power_db"][0]) == 512
        assert vis["hop_length"] == 128
        assert vis["peaks"]
    finally:
        p.unlink(missing_ok=True)


def test_visualisation_honors_oscilloscope_density() -> None:
    sr = 16000
    t = np.linspace(0, 5.0, int(sr * 5.0), endpoint=False)
    samples = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    vis = build_visualisation_layers(
        samples,
        sr,
        waveform_points=8192,
        spec_time_bins=1024,
        spec_freq_bins=256,
        fft_size=2048,
        hop_length=128,
        window_function="hann",
        db_floor=-96,
        db_ceiling=0,
        include_peaks=True,
        quality="oscilloscope",
    )
    assert vis["visualisation_status"] == "ready"
    assert vis["channels"] == 1
    assert vis["fft_size"] == 2048
    assert vis["hop_length"] == 128
    assert vis["window_function"] == "hann"
    assert len(vis["waveform"]["times"]) == 8192
    assert len(vis["spectrogram"]["frequencies"]) == 256
    assert len(vis["spectrogram"]["times"]) == 1024
    assert len(vis["spectrogram"]["power_db"]) == 256
    assert len(vis["spectrogram"]["power_db"][0]) == 1024
    assert vis["frequency_max_hz"] > 0
    assert vis["db_floor"] == -96
    assert vis["db_ceiling"] == 0
    assert vis["peaks"]
