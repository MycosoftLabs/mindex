"""Acoustic activity regions via Auditok."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def detect_activity_regions(path: Path) -> list[dict[str, Any]]:
    try:
        import auditok
    except ImportError as exc:
        raise RuntimeError("auditok required: pip install auditok") from exc

    events: list[dict[str, Any]] = []
    region_stream = auditok.split(
        str(path),
        min_dur=0.2,
        max_dur=30,
        max_silence=0.3,
        energy_threshold=55,
    )
    for idx, region in enumerate(region_stream):
        events.append(
            {
                "label": "acoustic_activity",
                "confidence": 0.85,
                "start_sec": float(region.start),
                "end_sec": float(region.end),
                "frequency_hz": None,
                "metadata": {
                    "region_index": idx,
                    "method": "auditok",
                },
            }
        )
    return events
