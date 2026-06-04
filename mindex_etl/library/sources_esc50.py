from __future__ import annotations

import csv
import io
import logging
import zipfile
from pathlib import Path
from typing import Any, Iterator

import httpx

logger = logging.getLogger(__name__)

ESC50_ZIP_URL = "https://github.com/karolpiczak/ESC-50/archive/refs/heads/master.zip"


def _load_esc50_meta(zf: zipfile.ZipFile) -> dict[str, dict[str, Any]]:
    """Parse meta/esc50.csv keyed by filename."""
    meta: dict[str, dict[str, Any]] = {}
    csv_name = None
    for name in zf.namelist():
        if name.lower().endswith("esc50.csv"):
            csv_name = name
            break
    if not csv_name:
        logger.warning("ESC-50: esc50.csv not found in archive")
        return meta
    with zf.open(csv_name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))
        for row in reader:
            fname = (row.get("filename") or "").strip()
            if fname:
                meta[fname] = row
    logger.info("ESC-50: loaded %s metadata rows", len(meta))
    return meta


def iter_esc50_audio(max_files: int) -> Iterator[tuple[bytes, str, dict[str, Any]]]:
    """Download ESC-50 zip; yield (wav_bytes, inner_path, meta_row)."""
    logger.info("Downloading ESC-50 archive...")
    with httpx.Client(timeout=600, follow_redirects=True) as client:
        resp = client.get(ESC50_ZIP_URL)
        resp.raise_for_status()
        data = resp.content
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        meta_by_file = _load_esc50_meta(zf)
        count = 0
        for name in zf.namelist():
            if not name.lower().endswith(".wav"):
                continue
            if "/audio/" not in name.lower() and "audio" not in Path(name).parts:
                if "ESC-50" not in name or ".wav" not in name.lower():
                    continue
            count += 1
            if count > max_files:
                break
            fname = Path(name).name
            row = meta_by_file.get(fname, {})
            yield zf.read(name), name, row
