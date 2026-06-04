"""Library wave annotations and human identifications (SINE player)."""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _json_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, default=str)


def _parse_uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_uuid") from exc


async def _require_acoustic_blob(blob_id: UUID, db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                SELECT id, category, duration_sec, filename
                FROM library.blob
                WHERE id = :id
                """
            ),
            {"id": blob_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="blob_not_found")
    data = dict(row)
    if data.get("category") != "acoustic":
        raise HTTPException(status_code=400, detail="acoustic_blob_required")
    return data


def _clamp_time(value: Any, duration: Optional[float]) -> float:
    try:
        t = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_time_sec") from exc
    if duration is not None and duration > 0:
        return max(0.0, min(t, float(duration)))
    return max(0.0, t)


def _normalize_selection(
    raw: Any, duration: Optional[float]
) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="invalid_selection")
    if "start_sec" not in raw and "end_sec" not in raw:
        return None
    start = _clamp_time(raw.get("start_sec", 0), duration)
    end = _clamp_time(raw.get("end_sec", start), duration)
    if end < start:
        start, end = end, start
    if end <= start:
        raise HTTPException(status_code=400, detail="invalid_selection_range")
    rate_raw = raw.get("playback_rate", 1)
    try:
        playback_rate = float(rate_raw)
    except (TypeError, ValueError):
        playback_rate = 1.0
    playback_rate = max(0.25, min(4.0, playback_rate))
    return {
        "start_sec": start,
        "end_sec": end,
        "loop_enabled": bool(raw.get("loop_enabled")),
        "reverse_enabled": bool(raw.get("reverse_enabled")),
        "playback_rate": playback_rate,
    }


def _normalize_zoom(raw: Any, duration: Optional[float]) -> Optional[dict[str, Any]]:
    if raw is None or not isinstance(raw, dict):
        return None
    if "start_sec" not in raw and "end_sec" not in raw:
        return None
    start = _clamp_time(raw.get("start_sec", 0), duration)
    end = _clamp_time(raw.get("end_sec", start), duration)
    if end < start:
        start, end = end, start
    return {"start_sec": start, "end_sec": end}


def _normalize_markers(raw: Any, duration: Optional[float]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="invalid_markers")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        time_sec = _clamp_time(item.get("time_sec", 0), duration)
        marker_id = item.get("id")
        out.append(
            {
                "id": str(marker_id) if marker_id is not None else None,
                "time_sec": time_sec,
                "label": label,
            }
        )
    return out


def _row_to_wave_annotation(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ("id", "blob_id", "analysis_run_id"):
        if data.get(key) is not None:
            data[key] = str(data[key])
    for key in ("selection", "zoom", "markers", "file_context"):
        val = data.get(key)
        if isinstance(val, str):
            try:
                data[key] = json.loads(val)
            except json.JSONDecodeError:
                pass
    if data.get("playback_rate") is not None:
        data["playback_rate"] = float(data["playback_rate"])
    return data


def _row_to_human_identification(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ("id", "blob_id", "analysis_run_id"):
        if data.get(key) is not None:
            data[key] = str(data[key])
    for key in ("model_summary", "event_context", "file_context"):
        val = data.get(key)
        if isinstance(val, str):
            try:
                data[key] = json.loads(val)
            except json.JSONDecodeError:
                pass
    for key in ("human_confidence", "model_confidence"):
        if data.get(key) is not None:
            data[key] = float(data[key])
    return data


def _review_status(human_label: str, model_top_label: Optional[str], disputes: bool) -> str:
    if not disputes:
        return "human_verified"
    if model_top_label and human_label.strip().lower() != model_top_label.strip().lower():
        return "contested_human_vs_model"
    return "human_tagged_pending_model_review"
