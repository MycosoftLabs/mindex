from __future__ import annotations

from mindex_api.worldview_snapshot_meta import snapshot_to_avani_meta


def test_snapshot_to_avani_meta_returns_degraded_missing_snapshot():
    meta = snapshot_to_avani_meta(None)

    assert meta["worldstate_snapshot_id"] is None
    assert meta["degraded"] is True
    assert meta["freshness"] == "unknown"
    assert meta["status"] == "missing"
    assert meta["confidence"] is None


def test_snapshot_to_avani_meta_preserves_audit_fields():
    meta = snapshot_to_avani_meta(
        {
            "snapshot_id": "worldstate-abc",
            "captured_at": "2026-05-14T12:00:00+00:00",
            "degraded": False,
            "confidence": 0.91,
            "provenance": {"source": "mas_worldstate"},
            "audit_trail_id": "audit-1",
        }
    )

    assert meta["worldstate_snapshot_id"] == "worldstate-abc"
    assert meta["captured_at"] == "2026-05-14T12:00:00+00:00"
    assert meta["freshness"] == "unknown"
    assert meta["degraded"] is True
    assert meta["confidence"] is None
    assert meta["reported_degraded"] is False
    assert meta["reported_confidence"] == 0.91
    assert meta["audit_trail_id"] == "audit-1"
