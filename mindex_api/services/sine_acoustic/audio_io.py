from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    import numpy as np


def _np():
    import numpy as np

    return np


def _load_wav_stdlib(path: Path) -> Tuple["np.ndarray", int]:
    import wave

    np = _np()
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        sw = wf.getsampwidth()
        ch = wf.getnchannels()
        raw = wf.readframes(n)
    if sw == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data.astype(np.float32), sr


def load_mono(path: Path, target_sr: int = 16000) -> Tuple["np.ndarray", int]:
    """Load audio as float32 mono; resample to target_sr when possible."""
    try:
        import soundfile as sf

        data, sr = sf.read(str(path), always_2d=True)
        mono = data.mean(axis=1).astype(_np().float32)
    except ImportError:
        mono, sr = _load_wav_stdlib(path)
    except Exception:
        mono, sr = _load_wav_stdlib(path)
    if sr != target_sr and len(mono) > 0:
        try:
            from scipy import signal

            n = int(len(mono) * target_sr / sr)
            mono = signal.resample(mono, n).astype(_np().float32)
            sr = target_sr
        except ImportError:
            pass
    return mono, sr
