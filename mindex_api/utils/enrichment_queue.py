"""
Enrichment Queue Utility
=======================
Appends viewed-but-incomplete taxa to a queue file for ancestry_sync to prioritize.
Used when a user views a species page with missing images/description.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _queue_file_path() -> Path:
    path = os.getenv("MINDEX_ENRICHMENT_QUEUE_FILE")
    if path:
        return Path(path)
    # Default: temp dir so mindex_api doesn't need mindex_etl config
    base = Path(os.getenv("TEMP", "/tmp")) / "mindex_ancestry"
    base.mkdir(parents=True, exist_ok=True)
    return base / "viewed_incomplete.jsonl"


def append_viewed_incomplete(taxon_id: str, name: str, missing: list[str] | None = None) -> None:
    """
    Append a taxon to the viewed-incomplete queue.
    ancestry_sync reads this file to prioritize enrichment.

    Call from get_taxon when taxon has missing image or description.
    """
    path = _queue_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry: Dict[str, Any] = {
        "taxon_id": str(taxon_id),
        "name": name,
        "at": datetime.utcnow().isoformat() + "Z",
        "missing": missing or [],
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Non-critical; enrichment queue is best-effort
