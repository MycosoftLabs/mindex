"""Map SINE detection events to Library / NatureOS frontend field shapes."""

from __future__ import annotations

from typing import Any


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _prototype_match_has_proof(row: dict[str, Any]) -> bool:
    """Require provenance before treating a prototype row as meaning."""
    has_identity = _has_value(row.get("prototype_id"))
    has_vector_proof = _has_value(row.get("vector_sha256")) or _has_value(row.get("prototype_sha256"))
    has_metric = row.get("score") is not None or row.get("distance") is not None
    return has_identity and has_vector_proof and has_metric


def _model_output_label(output: dict[str, Any]) -> Any:
    label = output.get("top_label") or output.get("label")
    if not label:
        labels = output.get("labels")
        if isinstance(labels, list) and labels:
            first = labels[0]
            label = first.get("label") if isinstance(first, dict) else first
    return label


def _model_output_has_proof(output: dict[str, Any]) -> bool:
    if str(output.get("ood_status") or "").lower() in {"low_confidence", "out_of_domain", "out_of_domain_candidate"}:
        return False
    has_model_identity = _has_value(output.get("model_id") or output.get("model_name"))
    has_model_provenance = _has_value(output.get("artifact_sha256")) or _has_value(output.get("label_map_sha256"))
    has_metric = output.get("confidence") is not None or output.get("ood_score") is not None
    return has_model_identity and has_model_provenance and has_metric


def _fusion_evidence_has_proof(row: dict[str, Any]) -> bool:
    has_link = _has_value(row.get("model_output_id")) or _has_value(row.get("prototype_match_id"))
    has_metric = row.get("score") is not None or row.get("weight") is not None
    return has_link and has_metric


def _sound_transcript_has_proof(row: dict[str, Any]) -> bool:
    return bool(row.get("model_output_ids") or row.get("fusion_evidence_ids") or row.get("prototype_ids"))


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
    deep_detections: list[dict[str, Any]] = []

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
            deep_detections.append(match)

    return {
        "frequency_detections": frequency,
        "activity_segments": activity,
        "bird_detections": bird,
        "uav_detections": uav,
        "nps_detections": nps,
        # Deep-signal detector rows are feature evidence, not proven neural
        # prototype matches. Keep them out of deep_signal_matches until a
        # registered embedding/prototype backend can prove provenance.
        "deep_signal_detections": deep_detections,
        "deep_signal_matches": [],
    }


def build_identification_summary(
    grouped: dict[str, list[dict[str, Any]]],
    *,
    detector_status: dict[str, str] | None = None,
    model_outputs: list[dict[str, Any]] | None = None,
    prototype_matches: list[dict[str, Any]] | None = None,
    fusion_evidence: list[dict[str, Any]] | None = None,
    sound_transcripts: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return a semantic identification only when model/prototype proof exists.

    The current SINE backend is detector-only. Labels such as ``bird_likely``
    and ``uav_rotor_likely`` are useful detector evidence, but they are not a
    top-level acoustic identity and must not be promoted into
    ``identification_summary`` until a registered model/prototype/fusion path
    exists.
    """
    _ = grouped
    for output in model_outputs or []:
        label = _model_output_label(output)
        if label and _model_output_has_proof(output):
            return {
                "top_label": label,
                "label": label,
                "confidence": output.get("confidence"),
                "ood_score": output.get("ood_score"),
                "status": "model_evidence",
                "engine": output.get("runtime") or output.get("framework"),
                "model": output.get("model_id") or output.get("model_name"),
                "detector_status": detector_status or {},
            }

    for row in fusion_evidence or []:
        label = row.get("label") or row.get("event_type") or row.get("event_family")
        if label and _fusion_evidence_has_proof(row):
            return {
                "top_label": label,
                "label": label,
                "confidence": row.get("score") or row.get("weight"),
                "status": "fusion_evidence",
                "engine": row.get("kind"),
                "model": row.get("model_id") or row.get("model_output_id"),
                "detector_status": detector_status or {},
            }

    for row in prototype_matches or []:
        label = row.get("label")
        if label and _prototype_match_has_proof(row):
            return {
                "top_label": label,
                "label": label,
                "confidence": row.get("score"),
                "ood_score": row.get("distance"),
                "status": "prototype_evidence",
                "engine": "prototype_match",
                "model": row.get("model_id"),
                "detector_status": detector_status or {},
            }

    for row in sound_transcripts or []:
        label = row.get("label")
        if label and _sound_transcript_has_proof(row):
            return {
                "top_label": label,
                "label": label,
                "confidence": row.get("confidence"),
                "status": "transcript_evidence",
                "engine": "sound_transcript",
                "model": None,
                "detector_status": detector_status or {},
            }
    return None


def build_library_classification_payload(
    events: list[dict[str, Any]],
    *,
    summary: dict[str, Any] | None = None,
    visualisation: dict[str, Any] | None = None,
    analysis_run_id: str | None = None,
    request_contract: dict[str, Any] | None = None,
    model_context: dict[str, Any] | None = None,
    model_outputs: list[dict[str, Any]] | None = None,
    prototype_matches: list[dict[str, Any]] | None = None,
    fusion_evidence: list[dict[str, Any]] | None = None,
    sound_transcripts: list[dict[str, Any]] | None = None,
    **_ignored: Any,
) -> dict[str, Any]:
    """Full acoustic classification view for Library blob detail + BFF.

    Tolerates extra persisted-evidence keys (e.g. ``deep_signal_matches``,
    a prototype-match alias) via ``**_ignored`` so the analyze route can splat
    ``list_persisted_analysis_evidence`` output without a signature mismatch.
    """
    detector_status = (summary or {}).get("detector_status") if summary else None
    grouped = group_events_for_library(events)
    outputs = model_outputs or []
    prototypes = prototype_matches or []
    fusion = fusion_evidence or []
    transcripts = sound_transcripts or []
    proven_outputs = [row for row in outputs if _model_output_has_proof(row) and _model_output_label(row)]
    proven_prototypes = [row for row in prototypes if _prototype_match_has_proof(row) and row.get("label")]
    proven_fusion = [
        row
        for row in fusion
        if _fusion_evidence_has_proof(row) and (row.get("label") or row.get("event_type") or row.get("event_family"))
    ]
    proven_transcripts = [row for row in transcripts if _sound_transcript_has_proof(row) and row.get("label")]
    identification = build_identification_summary(
        grouped,
        detector_status=detector_status,
        model_outputs=outputs,
        prototype_matches=prototypes,
        fusion_evidence=fusion,
        sound_transcripts=transcripts,
    )
    contract = request_contract or (summary or {}).get("request_contract") or {}
    model_info = model_context or (summary or {}).get("model_context") or {}
    has_model_evidence = bool(proven_outputs or proven_prototypes or proven_fusion or proven_transcripts)
    model_status = (
        "model_ready"
        if has_model_evidence
        else str(model_info.get("model_status") or "model_unavailable")
    )
    blocking_reasons = [] if has_model_evidence else list(model_info.get("blocking_reasons") or [])
    if not has_model_evidence and (outputs or prototypes or fusion or transcripts):
        blocking_reasons.append("unproven_model_or_prototype_evidence")
    detector_evidence = [
        *(grouped.get("bird_detections") or []),
        *(grouped.get("uav_detections") or []),
        *(grouped.get("nps_detections") or []),
        *(grouped.get("deep_signal_detections") or []),
    ]
    return {
        "analysis_run_id": analysis_run_id,
        "analysis_engine": "sine_acoustic",
        "model_status": model_status,
        "identification_status": identification.get("status") if identification else "detector_only",
        "identification_summary": identification,
        "request_contract": contract,
        "model_context": model_info,
        "model_outputs": outputs,
        "prototype_matches": prototypes,
        "fusion_evidence": fusion,
        "sound_transcripts": transcripts,
        "diagnostics": {
            "audio_decoded": True,
            "model_status": model_status,
            "model_ready": bool(model_info.get("model_ready", False) or has_model_evidence),
            "model_registry_ready": bool(model_info.get("model_registry_ready", False)),
            "prototype_catalog_ready": bool(model_info.get("prototype_catalog_ready", False) or proven_prototypes),
            "runtime_backends": model_info.get("runtime_backends") or {},
            "runtime_supported": bool(model_info.get("runtime_supported", False)),
            "inference_ready": bool(model_info.get("inference_ready", False) or has_model_evidence),
            "blocking_reasons": blocking_reasons,
            "request_contract": contract,
            "semantic_fallback_used": False,
            "llm_fallback_used": False,
            "filename_fallback_used": False,
            "metadata_fallback_used": False,
            "synthetic_output_used": False,
            "detector_status": detector_status or {},
        },
        "visualisation": visualisation,
        **grouped,
        "deep_signal_matches": prototypes,
        "acoustic_events": grouped["activity_segments"],
        "detector_evidence": detector_evidence,
        "pattern_matches": detector_evidence,
        "sine_matches": [],
    }
