"""SINE request/evidence contract helpers.

The Website BFF sends an evidence contract with every SINE analysis request.
MINDEX should preserve that contract in analysis summaries and diagnostics so
future model runtimes can be audited without inventing semantic labels.
"""

from __future__ import annotations

from typing import Any


SINE_EVIDENCE_QUERY_KEYS = (
    "require_real_audio",
    "require_model_evidence",
    "allow_detector_only",
    "semantic_fallback",
    "llm_fallback",
    "prototype_matching",
    "sound_transcripts",
)


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _query_map(request: Any) -> dict[str, str]:
    query_params = getattr(request, "query_params", {}) or {}
    return {
        key: str(query_params.get(key))
        for key in SINE_EVIDENCE_QUERY_KEYS
        if query_params.get(key) is not None
    }


async def read_sine_request_contract(request: Any) -> dict[str, Any]:
    """Read and normalize SINE evidence metadata from a FastAPI request.

    Invalid or empty request bodies are allowed. They should not break analysis;
    they simply mean the backend records a not-provided contract state.
    """
    body: Any = None
    body_status = "empty"
    try:
        body = await request.json()
        body_status = "json"
    except Exception:
        body = {}
        body_status = "empty_or_invalid"

    if not isinstance(body, dict):
        body = {}
        body_status = "non_object"

    evidence_contract = _dict_or_empty(body.get("evidence_contract"))
    sine_request = _dict_or_empty(body.get("sine_request"))
    evidence_query = _query_map(request)
    visualisation_quality = _dict_or_empty(sine_request.get("visualisation_quality"))
    requested_outputs = evidence_contract.get("requested_outputs")
    if not isinstance(requested_outputs, list):
        requested_outputs = []

    provided = bool(evidence_contract or sine_request or evidence_query)
    return {
        "contract_version": "sine-evidence-v1",
        "status": "provided" if provided else "not_provided",
        "body_status": body_status,
        "evidence_query": evidence_query,
        "evidence_contract": evidence_contract,
        "sine_request": sine_request,
        "requested_outputs": requested_outputs,
        "target_domains": sine_request.get("target_domains") if isinstance(sine_request.get("target_domains"), list) else [],
        "class_families": sine_request.get("class_families") if isinstance(sine_request.get("class_families"), list) else [],
        "sound_targets": sine_request.get("sound_targets") if isinstance(sine_request.get("sound_targets"), list) else [],
        "visualisation_quality": visualisation_quality,
        "requires_registered_model": bool(
            evidence_contract.get("require_registered_model_for_identification_summary")
            or evidence_contract.get("require_model_outputs_for_identification_summary")
            or evidence_query.get("require_model_evidence") == "true"
        ),
        "allows_detector_only": bool(
            evidence_contract.get("allow_detector_only_response")
            or evidence_query.get("allow_detector_only") == "true"
        ),
    }
