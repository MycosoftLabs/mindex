from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def iter_hf_hub_parquet_audio(
    repo_id: str,
    max_files: int,
    file_glob: str = "*.parquet",
) -> Iterator[tuple[Path, dict]]:
    """
    Download dataset parquet shards via huggingface_hub (no `datasets`/numpy).
    Caller must extract audio bytes from parquet if needed.
    """
    try:
        from huggingface_hub import HfApi, hf_hub_download, list_repo_files
    except ImportError as exc:
        logger.warning("huggingface_hub not installed: %s", exc)
        return

    api = HfApi()
    files = list_repo_files(repo_id, repo_type="dataset")
    audio_files = [
        f
        for f in files
        if f.lower().endswith((".wav", ".flac", ".mp3", ".ogg"))
    ][:max_files]
    count = 0
    for remote in audio_files:
        if count >= max_files:
            break
        try:
            local = hf_hub_download(repo_id, remote, repo_type="dataset")
            count += 1
            yield Path(local), {"hf_path": remote, "repo_id": repo_id}
        except Exception as exc:
            logger.warning("HF download failed %s: %s", remote, exc)
