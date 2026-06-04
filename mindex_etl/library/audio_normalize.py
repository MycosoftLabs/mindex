from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _probe_wav(path: Path) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            ch = w.getnchannels()
            dur = frames / float(rate) if rate else 0.0
            return {
                "sample_rate_hz": rate,
                "channels": ch,
                "duration_sec": round(dur, 4),
                "format": "wav",
                "codec": "pcm_s16le",
            }
    except Exception as exc:
        return {"probe_error": str(exc)}


def ffmpeg_available() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def normalize_to_wav(src: Path, dest: Path) -> dict[str, Any]:
    """
    Normalize audio to 16 kHz mono PCM WAV. Uses ffmpeg when present.
    Returns probe metadata + flags.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()
    meta: dict[str, Any] = {
        "source_path": str(src),
        "needs_transcode": False,
        "unsupported_codec": False,
    }

    if suffix == ".wav" and not ffmpeg_available():
        try:
            with wave.open(str(src), "rb") as w:
                if w.getframerate() == TARGET_SAMPLE_RATE and w.getnchannels() == TARGET_CHANNELS:
                    shutil.copy2(src, dest)
                    meta.update(_probe_wav(dest))
                    return meta
        except Exception:
            pass

    if ffmpeg_available():
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-ac",
            str(TARGET_CHANNELS),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode == 0 and dest.is_file():
            meta.update(_probe_wav(dest))
            return meta
        meta["ffmpeg_stderr"] = (proc.stderr or "")[:500]
        meta["needs_transcode"] = True

    if suffix in {".wav", ".flac"}:
        shutil.copy2(src, dest)
        meta.update(_probe_wav(dest))
        meta["needs_transcode"] = meta.get("sample_rate_hz") != TARGET_SAMPLE_RATE
        return meta

    meta["unsupported_codec"] = True
    meta["needs_transcode"] = True
    shutil.copy2(src, dest.with_suffix(src.suffix))
    return meta


def write_sidecar_manifest(path: Path, payload: dict[str, Any]) -> None:
    sidecar = path.with_suffix(path.suffix + ".manifest.json")
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
