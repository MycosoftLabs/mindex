#!/usr/bin/env python
"""Verify SINE real-AI evidence through the live MINDEX API.

This is the final proof gate after model/prototype registration. It calls the
API, optionally runs analysis on a UUID-backed acoustic blob, and fails unless
the response contains provenance-backed model outputs, prototype matches,
fusion evidence, and evidence-linked sound transcripts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


EVIDENCE_CONTRACT = {
    "evidence_contract": {
        "require_real_audio_decode": True,
        "require_explicit_model_status": True,
        "require_model_provenance_for_semantic_labels": True,
        "require_registered_model_for_identification_summary": True,
        "require_model_outputs_for_identification_summary": True,
        "require_runtime_artifact_checksum_for_model_outputs": True,
        "require_prototype_identity_for_deep_signal_matches": True,
        "require_vector_checksum_for_deep_signal_matches": True,
        "require_fusion_links_for_semantic_labels": True,
        "require_evidence_links_for_sound_transcripts": True,
        "allow_detector_only_response": True,
        "allow_llm_semantic_fallback": False,
        "allow_filename_semantic_fallback": False,
        "allow_metadata_semantic_fallback": False,
        "allow_mock_or_synthetic_outputs": False,
    },
    "sine_request": {
        "target_domains": ["water", "air", "ground"],
        "prototype_matching": True,
        "require_model_provenance": True,
        "require_prototype_vector_provenance": True,
        "require_chronological_sound_transcripts": True,
        "visualisation_quality": {
            "max_waveform_points": 8192,
            "max_time_frames": 1024,
            "max_frequency_bins": 256,
            "fft_size": 2048,
            "hop_length": 128,
            "include_peaks": True,
        },
    },
}


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _array(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nested(payload: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if current is not None:
            return current
    return None


def _model_output_has_proof(row: dict[str, Any]) -> bool:
    return (
        _has_value(row.get("model_id") or row.get("model_name"))
        and _has_value(row.get("artifact_sha256") or row.get("model_checksum"))
        and _has_value(row.get("label_map_sha256") or row.get("label_checksum"))
        and _has_value(row.get("top_label") or row.get("label"))
        and row.get("confidence") is not None
    )


def _prototype_has_proof(row: dict[str, Any]) -> bool:
    return (
        _has_value(row.get("prototype_id"))
        and _has_value(row.get("label"))
        and (row.get("score") is not None or row.get("distance") is not None)
        and _has_value(row.get("vector_sha256") or row.get("prototype_sha256") or row.get("vector_checksum"))
    )


def _prototype_match_model_id(row: dict[str, Any]) -> str:
    metadata = _object(row.get("metadata"))
    return str(row.get("model_id") or metadata.get("model_id") or "").strip()


def _prototype_catalog_has_proof(row: dict[str, Any]) -> bool:
    return (
        _has_value(row.get("prototype_id"))
        and _has_value(row.get("label"))
        and _has_value(row.get("model_id"))
        and _has_value(row.get("vector_sha256") or row.get("prototype_sha256") or row.get("vector_checksum"))
        and row.get("embedding_dim") is not None
    )


def _loaded_model_proofs(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = _array(payload, "models", "registered_models", "items")
    proofs: dict[str, dict[str, Any]] = {}
    for row in rows:
        model_id = str(row.get("model_id") or "").strip()
        if not model_id:
            continue
        loaded = bool(row.get("loaded")) or str(row.get("status") or "").lower() in {"model_ready", "ready", "loaded"}
        artifact_sha = str(row.get("artifact_sha256") or "").strip()
        label_map_sha = str(row.get("label_map_sha256") or "").strip()
        if loaded and artifact_sha and label_map_sha:
            proofs[model_id] = {**row, "artifact_sha256": artifact_sha, "label_map_sha256": label_map_sha}
    return proofs


def _prototype_catalog_proofs(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = _array(payload, "prototypes", "prototype_catalog", "items")
    return {str(row.get("prototype_id")): row for row in rows if _prototype_catalog_has_proof(row)}


def _fusion_has_proof(row: dict[str, Any]) -> bool:
    return (
        _has_value(row.get("model_output_id") or row.get("prototype_match_id") or row.get("detector_event_id"))
        and _has_value(row.get("label") or row.get("event_family") or row.get("event_type") or row.get("kind"))
        and (row.get("score") is not None or row.get("weight") is not None)
    )


def _transcript_has_proof(row: dict[str, Any]) -> bool:
    evidence_arrays = ("model_output_ids", "fusion_evidence_ids", "prototype_ids", "prototype_match_ids")
    return (
        _has_value(row.get("label"))
        and row.get("start_sec") is not None
        and row.get("end_sec") is not None
        and any(isinstance(row.get(key), list) and len(row.get(key) or []) > 0 for key in evidence_arrays)
    )


def validate_models(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _array(payload, "models", "registered_models", "items")
    failures: list[dict[str, Any]] = []
    loaded = [
        row
        for row in rows
        if bool(row.get("loaded")) or str(row.get("status") or "").lower() in {"model_ready", "ready", "loaded"}
    ]
    if not loaded:
        failures.append({"name": "model.loaded", "detail": "no loaded model rows"})
    for row in loaded:
        if not _has_value(row.get("artifact_sha256")):
            failures.append({"name": "model.artifact_sha256", "detail": row.get("model_id")})
        if not _has_value(row.get("label_map_sha256")):
            failures.append({"name": "model.label_map_sha256", "detail": row.get("model_id")})
    return failures


def validate_prototypes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _array(payload, "prototypes", "prototype_catalog", "items")
    proven = [row for row in rows if _prototype_catalog_has_proof(row)]
    if not proven:
        return [{"name": "prototype.catalog_rows", "detail": "no prototype catalog rows with id, label, model_id, embedding_dim, and checksum"}]
    return []


def validate_analysis(payload: dict[str, Any]) -> list[dict[str, Any]]:
    classification = payload.get("classification") if isinstance(payload.get("classification"), dict) else {}
    merged = {**classification, **payload}
    model_outputs = _array(merged, "model_outputs", "models", "model_predictions")
    prototypes = [
        *_array(merged, "prototype_matches", "prototypes"),
        *_array(merged, "deep_signal_matches", "deep_signal"),
    ]
    fusion = _array(merged, "fusion_evidence", "fusion", "evidence")
    transcripts = _array(merged, "sound_transcripts", "transcripts")
    failures: list[dict[str, Any]] = []
    if str(merged.get("model_status") or "").lower() != "model_ready":
        failures.append({"name": "analysis.model_status", "detail": merged.get("model_status")})
    if not any(_model_output_has_proof(row) for row in model_outputs):
        failures.append({"name": "analysis.model_outputs", "detail": "no provenance-backed model output"})
    if not any(_prototype_has_proof(row) for row in prototypes):
        failures.append({"name": "analysis.prototype_matches", "detail": "no checksum-backed prototype match"})
    if not any(_fusion_has_proof(row) for row in fusion):
        failures.append({"name": "analysis.fusion_evidence", "detail": "no linked fusion evidence"})
    if not any(_transcript_has_proof(row) for row in transcripts):
        failures.append({"name": "analysis.sound_transcripts", "detail": "no evidence-linked transcript"})
    identification = merged.get("identification_summary")
    if isinstance(identification, dict) and _has_value(identification.get("top_label") or identification.get("label")):
        if not (model_outputs or prototypes or fusion or transcripts):
            failures.append({"name": "analysis.identification_summary", "detail": "identity without evidence arrays"})
    return failures


def validate_cross_evidence_links(
    models_payload: dict[str, Any],
    prototypes_payload: dict[str, Any],
    analysis_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    classification = analysis_payload.get("classification") if isinstance(analysis_payload.get("classification"), dict) else {}
    merged = {**classification, **analysis_payload}
    model_outputs = _array(merged, "model_outputs", "models", "model_predictions")
    prototype_matches = [
        *_array(merged, "prototype_matches", "prototypes"),
        *_array(merged, "deep_signal_matches", "deep_signal"),
    ]
    loaded_models = _loaded_model_proofs(models_payload)
    catalog = _prototype_catalog_proofs(prototypes_payload)
    failures: list[dict[str, Any]] = []

    if not any(
        _model_output_has_proof(row)
        and str(row.get("model_id") or "").strip() in loaded_models
        and str(row.get("artifact_sha256") or "").strip() == loaded_models[str(row.get("model_id") or "").strip()].get("artifact_sha256")
        and str(row.get("label_map_sha256") or "").strip() == loaded_models[str(row.get("model_id") or "").strip()].get("label_map_sha256")
        for row in model_outputs
    ):
        failures.append({"name": "analysis.model_registry_link", "detail": "no model output matches a loaded registry artifact checksum and label-map checksum"})

    if not any(
        _prototype_has_proof(row)
        and str(row.get("prototype_id") or "").strip() in catalog
        and (
            not _prototype_match_model_id(row)
            or _prototype_match_model_id(row) == str(catalog[str(row.get("prototype_id") or "").strip()].get("model_id") or "").strip()
        )
        for row in prototype_matches
    ):
        failures.append({"name": "analysis.prototype_catalog_link", "detail": "no prototype match points at a registered checksum-backed prototype catalog row"})

    return failures


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = int(timeout)

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["X-Internal-Token"] = self.token
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed HTTP {exc.code}: {raw[:500]}") from exc


def choose_blob_id(payload: dict[str, Any]) -> str | None:
    rows = _array(payload, "items", "blobs", "files", "rows")
    for row in rows:
        value = row.get("id") or row.get("blob_id")
        if _has_value(value):
            return str(value)
    return None


def run_e2e(
    client: ApiClient,
    *,
    blob_id: str | None = None,
    search_query: str = "esc",
    run_analysis: bool = True,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    status_payload = client.request("GET", "/api/mindex/sine/status")
    models_payload = client.request("GET", "/api/mindex/sine/models")
    prototypes_payload = client.request("GET", "/api/mindex/sine/prototypes?limit=50")
    checks.extend({"status": "fail", **failure} for failure in validate_models(models_payload))
    checks.extend({"status": "fail", **failure} for failure in validate_prototypes(prototypes_payload))
    selected_blob_id = blob_id
    library_payload: dict[str, Any] | None = None
    if not selected_blob_id:
        query = urllib.parse.urlencode({"category": "acoustic", "q": search_query, "limit": 10})
        library_payload = client.request("GET", f"/api/mindex/library/blobs?{query}")
        selected_blob_id = choose_blob_id(library_payload)
    if not selected_blob_id:
        checks.append({"status": "fail", "name": "blob.selected", "detail": "no UUID-backed acoustic blob selected"})
        return {
            "status": "not_ready",
            "checks": checks,
            "status_payload": status_payload,
            "models_payload": models_payload,
            "prototypes_payload": prototypes_payload,
            "library_payload": library_payload,
        }
    analysis_payload = None
    if run_analysis:
        analysis_payload = client.request("POST", f"/api/mindex/sine/blobs/{selected_blob_id}/analyze", EVIDENCE_CONTRACT)
    else:
        analysis_payload = client.request("GET", f"/api/mindex/sine/blobs/{selected_blob_id}/analysis")
    checks.extend({"status": "fail", **failure} for failure in validate_analysis(analysis_payload))
    checks.extend(
        {"status": "fail", **failure}
        for failure in validate_cross_evidence_links(models_payload, prototypes_payload, analysis_payload)
    )
    return {
        "status": "ready" if not checks else "not_ready",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "blob_id": selected_blob_id,
        "checks": checks,
        "status_payload": status_payload,
        "models_payload": models_payload,
        "prototypes_payload": prototypes_payload,
        "analysis_payload": analysis_payload,
        "library_payload": library_payload,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=os.environ.get("MINDEX_API_URL", "http://192.168.0.189:8000"))
    parser.add_argument("--token", default=os.environ.get("MINDEX_INTERNAL_TOKEN") or os.environ.get("MINDEX_INTERNAL_TOKENS"))
    parser.add_argument("--blob-id")
    parser.add_argument("--query", default="esc")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--no-run-analysis", action="store_true", help="Read latest analysis instead of POSTing a new one.")
    parser.add_argument("--write-report", help="Optional JSON report path.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    token = None
    if args.token:
        token = str(args.token).split(",")[0].strip()
    report = run_e2e(
        ApiClient(str(args.api_base), token=token, timeout=int(args.timeout)),
        blob_id=args.blob_id,
        search_query=str(args.query),
        run_analysis=not bool(args.no_run_analysis),
    )
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.write_report:
        with open(args.write_report, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
    print(output)
    if report["status"] != "ready":
        sys.exit(1)


if __name__ == "__main__":
    main()
