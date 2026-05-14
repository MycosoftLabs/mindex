"""Dependency-light helpers for Worldview snapshot AVANI metadata."""

from __future__ import annotations

from typing import Any, Dict, Optional


def snapshot_to_avani_meta(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not snapshot:
        return {
            "worldstate_snapshot_id": None,
            "freshness": "degraded",
            "degraded": True,
            "confidence": 0.35,
            "provenance": {"source": "mindex_worldview_snapshot_store", "snapshot": "missing"},
            "audit_trail_id": None,
        }
    return {
        "worldstate_snapshot_id": snapshot.get("snapshot_id"),
        "freshness": snapshot.get("captured_at"),
        "degraded": bool(snapshot.get("degraded")),
        "confidence": snapshot.get("confidence"),
        "provenance": snapshot.get("provenance") or {},
        "audit_trail_id": snapshot.get("audit_trail_id"),
    }
