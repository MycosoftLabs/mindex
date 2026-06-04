from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


def iter_hf_audio_dataset(
    dataset_id: str,
    audio_column: str,
    max_files: int,
    split: str = "train",
) -> Iterator[tuple[Path, dict]]:
    """
    Stream audio files from a HuggingFace dataset that exposes decodable audio.
    Yields (temp_path, row_metadata).
    """
    try:
        from datasets import load_dataset
    except ImportError as exc:
        logger.warning("datasets package not installed: %s", exc)
        return

    logger.info("Loading HuggingFace dataset %s (split=%s)...", dataset_id, split)
    try:
        ds = load_dataset(dataset_id, split=split, trust_remote_code=True)
    except Exception as exc:
        logger.error("HF load failed for %s: %s", dataset_id, exc)
        return

    count = 0
    for row in ds:
        if count >= max_files:
            break
        audio = row.get(audio_column) or row.get("audio")
        if not audio:
            continue
        path: Optional[str] = None
        if isinstance(audio, dict):
            path = audio.get("path")
            if not path and "bytes" in audio:
                suffix = ".wav"
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                tmp.write(audio["bytes"])
                tmp.flush()
                tmp.close()
                path = tmp.name
        elif isinstance(audio, str):
            path = audio
        if not path or not Path(path).is_file():
            continue
        count += 1
        meta = {k: row[k] for k in row if k != audio_column and k != "audio"}
        yield Path(path), meta
