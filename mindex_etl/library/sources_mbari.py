from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# AWS Open Data Registry (us-west-2) — prefer 2 kHz decimated bucket (smaller files)
MBARI_BUCKETS = (
    "pacific-sound-2khz",
    "pacific-sound-256khz-2024",
    "pacific-sound-256khz-2023",
    "pacific-sound-256khz-2022",
    "pacific-sound-256khz-2021",
    "pacific-sound-256khz-2020",
    "pacific-sound-256khz-2019",
    "pacific-sound-256khz-2018",
)
AUDIO_SUFFIXES = {".wav", ".flac", ".mp3"}


def iter_mbari_s3(max_files: int, prefix: str = "") -> Iterator[tuple[Path, str, str]]:
    """List and download open MBARI Pacific Sound objects (bounded)."""
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config
    except ImportError as exc:
        logger.warning("boto3 not installed: %s", exc)
        return

    count = 0
    for bucket in MBARI_BUCKETS:
        if count >= max_files:
            return
        s3 = boto3.client(
            "s3",
            config=Config(signature_version=UNSIGNED),
            region_name="us-west-2",
        )
        try:
            s3.head_bucket(Bucket=bucket)
        except Exception:
            logger.info("MBARI bucket unavailable: %s", bucket)
            continue
        logger.info("MBARI listing bucket %s", bucket)
        paginator = s3.get_paginator("list_objects_v2")
        try:
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
        except Exception as exc:
            logger.warning("MBARI list failed %s: %s", bucket, exc)
            continue
        for page in pages:
            for obj in page.get("Contents") or []:
                key = obj.get("Key") or ""
                if not any(key.lower().endswith(s) for s in AUDIO_SUFFIXES):
                    continue
                if count >= max_files:
                    return
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(key).suffix)
                tmp.close()
                dest = Path(tmp.name)
                try:
                    s3.download_file(bucket, key, str(dest))
                    count += 1
                    yield dest, bucket, key
                except Exception as exc:
                    logger.warning("S3 download failed %s: %s", key, exc)
                    if dest.is_file():
                        dest.unlink(missing_ok=True)
