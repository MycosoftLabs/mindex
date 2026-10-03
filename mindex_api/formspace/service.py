"""Application orchestration. A compute receipt never substitutes for archive proof."""
from __future__ import annotations

import hmac
import json
import re

from .contracts import (FormSpaceError, MAX_RESULT_BYTES, canonical, digest, receipt)


def strict_json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError("nonfinite JSON value")
    return json.loads(payload, object_pairs_hook=pairs, parse_constant=nonfinite)


class FormSpaceService:
    def __init__(self, repository, retention_service, engine_code_sha256, *, admission_metadata_factory=None):
        self.repository, self.retention = repository, retention_service
        self.engine_code_sha256 = engine_code_sha256
        self.admission_metadata_factory = admission_metadata_factory

    def enabled(self):
        if not re.fullmatch(r"[0-9a-f]{64}", self.engine_code_sha256 or ""):
            raise FormSpaceError("engine_version_unconfigured")

    async def computed(self, job_id, body):
        self.enabled()
        lease = {key: body[key] for key in ("lease_token", "fence")}
        row = await self.repository.leased(job_id, lease)
        payload = body["result_json"].encode("utf-8")
        if len(payload) > MAX_RESULT_BYTES:
            raise FormSpaceError("result_too_large", 413)
        if not hmac.compare_digest(digest(payload), body["output_sha256"]):
            raise FormSpaceError("output_hash_mismatch", 422)
        try:
            result = strict_json(payload)
            expected_keys = {"schema", "engine_mode", "status", "reason", "chart_id", "chart_revision",
                "dataset_id", "classification", "data_origin", "as_of", "sample_ids", "masked_sample_ids",
                "parameters", "baseline_trajectory", "perturbed_trajectory", "residuals_after_perturbation",
                "first_crossing_index", "first_crossing_elapsed_seconds", "baseline_final_state",
                "perturbed_final_state", "interpretation", "hashes"}
            if not isinstance(result, dict) or set(result) != expected_keys:
                raise ValueError("invalid result fields")
            request = row["request"]
            if isinstance(request, str):
                request = strict_json(request)
            hashes = result["hashes"]
            if not isinstance(hashes, dict) or set(hashes) != {"code", "input", "chart_revision",
                    "dataset", "parameters", "output"}:
                raise ValueError("invalid hash fields")
            expected = {"code": self.engine_code_sha256, "input": row["request_hash"],
                        "chart_revision": row["chart_hash"], "dataset": row["dataset_hash"],
                        "parameters": digest(canonical(request["parameters"]))}
            if any(hashes.get(key) != value for key, value in expected.items()):
                raise ValueError("lineage mismatch")
            copied = {**result, "hashes": {key: value for key, value in hashes.items() if key != "output"}}
            if hashes["output"] != digest(canonical(copied)):
                raise ValueError("output content hash mismatch")
            if (result["schema"] != "formspace.experiment.result/v1"
                    or result["engine_mode"] != "scalar-native-v1"
                    or result["classification"] != "PRIVATE"
                    or result["chart_id"] != request["chart_revision"]["chart_id"]
                    or result["chart_revision"] != request["chart_revision"]["revision"]
                    or result["dataset_id"] != request["dataset"]["dataset_id"]
                    or result["data_origin"] != request["dataset"]["source"]["kind"]
                    or result["as_of"] != request["as_of"]
                    or canonical(result["parameters"]) != canonical(request["parameters"])):
                raise ValueError("result scope mismatch")
            samples = request["dataset"]["samples"]
            masked = [sample["sample_id"] for sample in samples if sample["masked"]]
            if result["sample_ids"] != [sample["sample_id"] for sample in samples]:
                raise ValueError("sample IDs mismatch")
            if result["masked_sample_ids"] != masked:
                raise ValueError("mask mismatch")
            if result["status"] != ("abstained" if masked else "computed"):
                raise ValueError("mask abstention mismatch")
            if result["reason"] != ("MASKED_INPUT" if masked else None):
                raise ValueError("invalid reason")
            if not isinstance(result["interpretation"], str) or not 1 <= len(result["interpretation"]) <= 4096:
                raise ValueError("invalid interpretation")
            lengths = (0, 0, 0) if masked else (len(samples), len(samples),
                        len(samples) - request["parameters"]["perturbation_index"])
            for field, size in zip(("baseline_trajectory", "perturbed_trajectory",
                                    "residuals_after_perturbation"), lengths):
                if len(result[field]) != size or any(type(value) not in (int, float)
                        or not -1e18 <= value <= 1e18 for value in result[field]):
                    raise ValueError("invalid scalar output")
            if masked:
                if any(result[field] is not None for field in ("first_crossing_index",
                        "first_crossing_elapsed_seconds", "baseline_final_state", "perturbed_final_state")):
                    raise ValueError("abstention cannot contain scalar conclusions")
            else:
                baseline, perturbed = result["baseline_trajectory"], result["perturbed_trajectory"]
                index = request["parameters"]["perturbation_index"]
                residuals = [abs(b - a) for a, b in zip(baseline[index:], perturbed[index:])]
                if result["residuals_after_perturbation"] != residuals:
                    raise ValueError("residual mismatch")
                for field, expected_final in (("baseline_final_state", baseline[-1]),
                                              ("perturbed_final_state", perturbed[-1])):
                    if type(result[field]) not in (int, float) or result[field] != expected_final:
                        raise ValueError("final state mismatch")
                offset = next((i for i, value in enumerate(residuals)
                               if value <= request["parameters"]["residual_threshold"]), None)
                crossing = None if offset is None else index + offset
                elapsed = None if offset is None else offset * request["parameters"]["dt"]
                if (result["first_crossing_index"] != crossing
                        or result["first_crossing_elapsed_seconds"] != elapsed
                        or (crossing is not None and type(result["first_crossing_index"]) is not int)
                        or (elapsed is not None and type(result["first_crossing_elapsed_seconds"]) not in (int, float))):
                    raise ValueError("threshold crossing mismatch")
            if canonical(result) != payload:
                raise ValueError("result must use canonical bytes")
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
            raise FormSpaceError("invalid_result", 422) from exc
        return await self.repository.computed(job_id, lease, payload, body["output_sha256"])

    async def reconcile(self, job_id, lease):
        self.enabled()
        row = await self.repository.leased(job_id, lease)
        if row["output_bytes"] is None or row["state"] != "archiving":
            raise FormSpaceError("result_not_computed", 409)
        principal = self.repository.owner(row)
        payload = bytes(row["output_bytes"])
        if digest(payload) != row["output_sha256"]:
            raise FormSpaceError("persisted_result_integrity_failed", 502)
        # Shared retention owns the final private artifact, archive, and outbox.
        # App bytes survive the crash window between this transaction and link.
        admission_metadata = self.admission_metadata_factory
        if admission_metadata is None:
            from ..retention.contracts import admission_metadata
        metadata = admission_metadata("artifact", "formspace:" + job_id,
                                      "application/json", None, self.retention.config)
        artifact, _ = await self.retention.admit(principal, metadata, payload)
        if artifact["state"] in ("cancelled", "deleted", "quarantined"):
            raise FormSpaceError("retained_artifact_unavailable", 409)
        verified = artifact["state"] == "verified"
        if verified:
            proof, downloaded = await self.retention.content(principal, str(artifact["artifact_id"]))
            if (downloaded != payload or proof["sha256"] != row["output_sha256"]):
                raise FormSpaceError("artifact_integrity_failed", 502)
        # Rechecks membership and lease after external I/O. Cancel/revoke wins.
        saved = await self.repository.retained(job_id, lease, artifact, verified=verified)
        if verified:
            try:
                return await self.remember(principal, job_id)
            except Exception:
                # Memory proof is independent; caller sees completed artifact,
                # pending memory and can retry the authenticated memory endpoint.
                return saved
        return saved

    async def remember(self, principal, job_id):
        row = await self.repository.get(principal, job_id)
        if row["state"] != "completed" or not row["artifact_id"]:
            raise FormSpaceError("result_not_verified", 409)
        proof = await self.retention.remember(principal, str(row["artifact_id"]),
            "FormSpace scalar-native-v1 experiment " + job_id + "; private result; no model training claim.")
        if proof.get("reference_verified") is not True or proof.get("artifact_sha256") != row["output_sha256"]:
            raise FormSpaceError("memory_reference_unverified", 502)
        await self.repository.memory(principal, job_id, "verified", str(proof["memory_id"]))
        return receipt(await self.repository.get(principal, job_id))

    async def result(self, principal, job_id):
        row = await self.repository.get(principal, job_id)
        if row["state"] != "completed" or not row["artifact_id"]:
            raise FormSpaceError("result_not_verified", 409)
        artifact_id = str(row["artifact_id"])
        before = await self.retention.repository.get(principal, artifact_id)
        proof, payload = await self.retention.content(principal, artifact_id)
        current = await self.repository.get(principal, job_id)
        artifact = await self.retention.repository.get(principal, artifact_id)
        if (current["state"] != "completed" or str(current["artifact_id"]) != artifact_id
                or len(payload) > MAX_RESULT_BYTES or digest(payload) != current["output_sha256"]
                or proof["sha256"] != current["output_sha256"] or artifact["state"] != "verified"
                or artifact.get("object_version") != before.get("object_version")
                or not artifact.get("object_version")):
            raise FormSpaceError("result_integrity_failed", 502)
        return payload, current["output_sha256"], artifact["object_version"]

    async def input(self, principal, job_id):
        row = await self.repository.get(principal, job_id)
        request = row["request"]
        payload = canonical(strict_json(request) if isinstance(request, str) else request)
        if digest(payload) != row["request_hash"]:
            raise FormSpaceError("input_integrity_failed", 502)
        return payload, row["request_hash"]
