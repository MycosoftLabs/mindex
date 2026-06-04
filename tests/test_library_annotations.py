"""Unit tests for library wave/human annotation helpers."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from mindex_api.services.library_annotations import (
    _normalize_markers,
    _normalize_selection,
    _review_status,
)


def test_normalize_selection_orders_endpoints():
    sel = _normalize_selection({"start_sec": 5.0, "end_sec": 2.0}, 10.0)
    assert sel is not None
    assert sel["start_sec"] == 2.0
    assert sel["end_sec"] == 5.0


def test_normalize_selection_rejects_zero_span():
    with pytest.raises(HTTPException) as exc:
        _normalize_selection({"start_sec": 3.0, "end_sec": 3.0}, 10.0)
    assert exc.value.detail == "invalid_selection_range"


def test_normalize_markers_clamps_to_duration():
    markers = _normalize_markers([{"time_sec": 99, "label": "peak"}], 5.0)
    assert markers[0]["time_sec"] == 5.0


def test_review_status_contested():
    assert _review_status("lightning", "UAV", True) == "contested_human_vs_model"
