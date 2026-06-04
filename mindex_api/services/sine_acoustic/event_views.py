"""Map SINE detection events to Library / NatureOS frontend field shapes."""

from __future__ import annotations

from typing import Any


def _event_to_match(ev: dict[str, Any]) -> dict[str, Any]:
    """Normalize DB/API event to AcousticPatternMatch-friendly keys."""
    start = ev.get("start_sec")
    if start is None:
        start = ev.get("start_seconds")
    end = ev.get("end_sec")
    if end is None:
        end = ev.get("end_seconds")
    peak = ev.get("peak_seconds")
    if peak is None and start is not None:
        peak = start
    meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
    return {
        "id": ev.get("id"),
        "label": ev.get("label"),
        "class_name": ev.get("label"),
        "confidence": ev.get("confidence"),
        "start_seconds": start,
        "end_seconds": end,
        "peak_seconds": peak,
        "frequency_hz": ev.get("frequency_hz"),
        "category": meta.get("category") or _category_from_detector(ev.get("detector_id")),
        "engine": meta.get("method") or ev.get("detector_id"),
        "model": meta.get("upstream") or meta.get("method"),
        "metadata": meta,
    }


def _category_from_detector(detector_id: str | None) -> str | None:
    if not detector_id:
        return None
    if "bird" in detector_id:
        return "bird"
    if "uav" in detector_id:
        return "uav"
    if "nps" in detector_id:
        return "nps"
    if "frequency" in detector_id:
        return "frequency"
    if "activity" in detector_id:
        return "activity"
    if "deep_signal" in detector_id:
        return "deep_signal"
    return "acoustic"


def group_events_for_library(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split flat detection_event rows into Library tab arrays."""
    frequency: list[dict[str, Any]] = []
    activity: list[dict[str, Any]] = []
    bird: list[dict[str, Any]] = []
    uav: list[dict[str, Any]] = []
    nps: list[dict[str, Any]] = []
    deep: list[dict[str, Any]] = []

    for raw in events:
        det = str(raw.get("detector_id") or "")
        match = _event_to_match(raw)
        if det == "frequency_fft":
            frequency.append(match)
        elif det == "activity_auditok":
            activity.append(match)
        elif det == "bird_microsoft":
            bird.append(match)
        elif det == "uav_rotor":
            uav.append(match)
        elif det == "nps_discovery_match":
            nps.append(match)
        elif det == "deep_signal_features":
            deep.append(match)

    return {
        "frequency_detections": frequency,
        "activity_segments": activity,
        "bird_detections": bird,
        "uav_detections": uav,
        "nps_detections": nps,
        "deep_signal_matches": deep,
    }


def build_identification_summary(
    grouped: dict[str, list[dict[str, Any]]],
    *,
    detector_status: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Single top-level acoustic identification for Library header."""
    candidates: list[tuple[float, str, str]] = []

    for match in grouped.get("bird_detections") or []:
        conf = float(match.get("confidence") or 0)
        label = str(match.get("label") or "bird")
        candidates.append((conf, label, "bird_microsoft"))

    for match in grouped.get("uav_detections") or []:
        conf = float(match.get("confidence") or 0)
        label = str(match.get("label") or "uav")
        candidates.append((conf, label, "uav_rotor"))

    for match in grouped.get("nps_detections") or []:
        conf = float(match.get("confidence") or 0)
        label = str(match.get("label") or "nps_match")
        candidates.append((conf, label, "nps_discovery_match"))

    for match in grouped.get("deep_signal_matches") or []:
        conf = float(match.get("confidence") or 0)
        label = str(match.get("label") or "pattern")
        candidates.append((conf, label, "deep_signal_features"))

    top_label = "unclassified"
    top_conf = 0.0
    engine = "sine_acoustic"
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        top_conf, top_label, engine = candidates[0]

    freq = grouped.get("frequency_detections") or []
    top_hz = freq[0].get("frequency_hz") if freq else None

    return {
        "top_label": top_label,
        "label": top_label,
        "confidence": round(top_conf, 4) if top_conf else None,
        "engine": engine,
        "model": "mindex_sine_v1",
        "status": "classified" if candidates or freq else "pending",
        "dominant_frequency_hz": top_hz,
        "detector_status": detector_status or {},
        "detector_counts": {
            "frequency": len(freq),
            "activity": len(grouped.get("activity_segments") or []),
            "bird": len(grouped.get("bird_detections") or []),
            "uav": len(grouped.get("uav_detections") or []),
            "nps": len(grouped.get("nps_detections") or []),
            "deep_signal": len(grouped.get("deep_signal_matches") or []),
        },
    }


def build_library_classification_payload(
    events: list[dict[str, Any]],
    *,
    summary: dict[str, Any] | None = None,
    visualisation: dict[str, Any] | None = None,
    analysis_run_id: str | None = None,
) -> dict[str, Any]:
    """Full acoustic classification view for Library blob detail + BFF."""
    detector_status = (summary or {}).get("detector_status") if summary else None
    grouped = group_events_for_library(events)
    identification = build_identification_summary(grouped, detector_status=detector_status)
    return {
        "analysis_run_id": analysis_run_id,
        "analysis_engine": "sine_acoustic",
        "identification_status": identification.get("status"),
        "identification_summary": identification,
        "visualisation": visualisation,
        **grouped,
        "acoustic_events": grouped["activity_segments"],
        "pattern_matches": [
            *(grouped.get("bird_detections") or []),
            *(grouped.get("uav_detections") or []),
            *(grouped.get("nps_detections") or []),
            *(grouped.get("deep_signal_matches") or []),
        ],
        "sine_matches": grouped.get("deep_signal_matches") or [],
    }
