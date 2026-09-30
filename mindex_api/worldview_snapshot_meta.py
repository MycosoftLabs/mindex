"""Dependency-light helpers for Worldview snapshot AVANI metadata."""

from __future__ import annotations

from typing import Any, Dict, Optional


def unavailable_snapshot_meta(status: str) -> Dict[str, Any]:
    """Absence/availability carries no invented scientific confidence score."""
    return {
        "status": status,
        "worldstate_snapshot_id": None,
        "freshness": "unknown",
        "degraded": True,
        "confidence": None,
        "captured_at": None,
        "source_freshness": {},
        "reported_confidence": None,
        "reported_degraded": None,
        "evidence_status": "source_reported_unverified",
        "provenance": {"source": "mindex_worldview_snapshot_store", "snapshot": status},
        "audit_trail_id": None,
    }


def snapshot_to_avani_meta(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Preserve declared evidence; capture time alone is not a freshness assessment."""
    if not snapshot:
        return unavailable_snapshot_meta("missing")
    return {
        "status": "available",
        "worldstate_snapshot_id": snapshot.get("snapshot_id"),
        "freshness": "unknown",
        "degraded": True,
        "confidence": None,
        "captured_at": snapshot.get("captured_at"),
        "source_freshness": snapshot.get("source_freshness") or {},
        "reported_confidence": snapshot.get("confidence"),
        "reported_degraded": None if snapshot.get("degraded") is None else bool(snapshot["degraded"]),
        "evidence_status": "source_reported_unverified",
        "provenance": snapshot.get("provenance") or {},
        "audit_trail_id": snapshot.get("audit_trail_id"),
    }


def public_snapshot_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Do not newly expose arbitrary source maps or private snapshot payloads."""
    fields = (
        "status", "worldstate_snapshot_id", "freshness", "degraded", "confidence",
        "captured_at", "reported_confidence", "reported_degraded", "evidence_status",
        "provenance", "audit_trail_id",
    )
    return {
        **{key: meta.get(key) for key in fields},
        "source_freshness_available": bool(meta.get("source_freshness")),
        "source_freshness_assessment": "unknown",
    }
