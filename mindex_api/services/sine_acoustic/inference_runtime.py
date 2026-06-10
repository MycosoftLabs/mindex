"""SINE acoustic model inference runtime.

This module is the real model execution seam for future PyTorch/TorchScript and
ONNX artifacts. It never fabricates labels: if a local artifact, label map, or
runtime dependency is missing, it reports an unavailable status instead of
returning semantic output.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from .features import extract_sine_feature_tensor
from .model_runtime import artifact_path_from_uri, runtime_backend_status, sha256_file
from .prototype_search import vector_from_value, vector_sha256


def _status(status: str, *, detail: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "ok": False,
        "status": status,
        "model_status": "model_unavailable",
        "detail": detail,
        "model_outputs": [],
    }
    payload.update(extra)
    return payload


def _normalize_runtime(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "")


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _read_label_map(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        labels = [str(item) for item in raw]
    elif isinstance(raw, dict):
        if isinstance(raw.get("labels"), list):
            labels = [str(item) for item in raw["labels"]]
        elif isinstance(raw.get("classes"), list):
            labels = [str(item) for item in raw["classes"]]
        else:
            pairs = []
            for key, value in raw.items():
                try:
                    pairs.append((int(key), str(value)))
                except (TypeError, ValueError):
                    continue
            labels = [value for _, value in sorted(pairs)]
    else:
        labels = []
    if not labels:
        raise ValueError("label map did not contain labels")
    return labels


def _stable_probabilities(values: np.ndarray) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32).reshape(-1)
    if vector.size == 0:
        return vector
    finite = np.nan_to_num(vector, nan=-1e9, posinf=1e9, neginf=-1e9)
    if np.all(finite >= 0) and 0.98 <= float(finite.sum()) <= 1.02:
        return finite / max(float(finite.sum()), 1e-12)
    shifted = finite - np.max(finite)
    exp = np.exp(shifted)
    return exp / max(float(exp.sum()), 1e-12)


def _top_k_labels(probabilities: np.ndarray, labels: list[str], top_k: int) -> list[dict[str, Any]]:
    count = min(max(1, int(top_k or 1)), len(labels), probabilities.size)
    if count <= 0:
        return []
    indices = np.argsort(probabilities)[::-1][:count]
    return [
        {
            "index": int(index),
            "label": labels[int(index)],
            "confidence": float(probabilities[int(index)]),
        }
        for index in indices
    ]


def _float_param(*values: Any, default: float) -> float:
    for value in values:
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return float(default)


def _ood_metrics(
    probabilities: np.ndarray,
    *,
    min_confidence: float = 0.0,
    ood_threshold: float = 1.0,
) -> dict[str, Any]:
    """Return transparent open-set confidence metrics for a probability vector."""
    vector = np.asarray(probabilities, dtype=np.float32).reshape(-1)
    if vector.size == 0:
        return {
            "confidence": 0.0,
            "confidence_margin": 0.0,
            "entropy": 0.0,
            "normalized_entropy": 1.0,
            "ood_score": 1.0,
            "ood_status": "out_of_domain_candidate",
            "ood_threshold": float(ood_threshold),
            "min_confidence": float(min_confidence),
        }
    ordered = np.sort(vector)[::-1]
    top = float(ordered[0])
    second = float(ordered[1]) if ordered.size > 1 else 0.0
    margin = max(0.0, top - second)
    safe = np.clip(vector, 1e-12, 1.0)
    entropy = float(-np.sum(safe * np.log(safe)))
    normalized_entropy = float(entropy / np.log(vector.size)) if vector.size > 1 else 0.0
    # Higher score means more uncertainty: low top probability, low margin,
    # and high normalized entropy all contribute.
    ood_score = float(np.clip(((1.0 - top) + (1.0 - margin) + normalized_entropy) / 3.0, 0.0, 1.0))
    if top < min_confidence:
        status = "low_confidence"
    elif ood_score >= ood_threshold:
        status = "out_of_domain_candidate"
    else:
        status = "in_domain_candidate"
    return {
        "confidence": top,
        "confidence_margin": margin,
        "entropy": entropy,
        "normalized_entropy": normalized_entropy,
        "ood_score": ood_score,
        "ood_status": status,
        "ood_threshold": float(ood_threshold),
        "min_confidence": float(min_confidence),
    }


def _to_numpyish(value: Any) -> Any:
    """Convert torch/numpy/list/dict outputs into numpy/list/dict structures."""
    if hasattr(value, "detach") and callable(value.detach):
        value = value.detach()
    if hasattr(value, "cpu") and callable(value.cpu):
        value = value.cpu()
    if hasattr(value, "numpy") and callable(value.numpy):
        return value.numpy()
    if isinstance(value, np.ndarray):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_numpyish(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_numpyish(item) for key, item in value.items()}
    return value


def _output_parts(raw_output: Any) -> tuple[np.ndarray, list[float]]:
    """Split model output into classification scores and optional embedding."""
    output = _to_numpyish(raw_output)
    embedding: list[float] = []
    scores: Any = output
    if isinstance(output, dict):
        for key in ("embedding", "embedding_vector", "feature_embedding", "vector"):
            embedding = vector_from_value(output.get(key))
            if embedding:
                break
        for key in ("logits", "scores", "probabilities", "classification", "output"):
            if key in output:
                scores = output[key]
                break
    elif isinstance(output, list):
        if output:
            scores = output[0]
        if len(output) > 1:
            embedding = vector_from_value(output[1])
    return np.asarray(scores, dtype=np.float32), embedding


def _run_torchscript(artifact_path: Path, tensor: np.ndarray) -> Any:
    import torch

    model = torch.jit.load(str(artifact_path), map_location="cpu")
    model.eval()
    with torch.no_grad():
        output = model(torch.from_numpy(tensor.astype(np.float32)))
    return _to_numpyish(output)


def _run_onnx(artifact_path: Path, tensor: np.ndarray) -> Any:
    import onnxruntime as ort

    session = ort.InferenceSession(str(artifact_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    return session.run(None, {input_name: tensor.astype(np.float32)})


def run_registered_model_inference(
    samples: np.ndarray,
    sample_rate: int,
    model_record: dict[str, Any],
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    """Run a registered local model artifact on a real audio window."""
    model_id = str(model_record.get("model_id") or "").strip()
    if not model_id:
        return _status("model_record_invalid", detail="model_id is required")

    artifact_path = artifact_path_from_uri(str(model_record.get("artifact_uri") or ""))
    if artifact_path is None:
        return _status("model_artifact_invalid", detail="only local model artifact paths are supported", model_id=model_id)
    if not artifact_path.exists():
        return _status("model_artifact_missing", detail=f"model artifact not found: {artifact_path}", model_id=model_id)

    expected_artifact_sha = str(model_record.get("artifact_sha256") or "").strip().lower()
    actual_artifact_sha = sha256_file(artifact_path).lower()
    if expected_artifact_sha and actual_artifact_sha != expected_artifact_sha:
        return _status(
            "model_artifact_checksum_mismatch",
            detail="model artifact checksum did not match registry",
            model_id=model_id,
            artifact_sha256=actual_artifact_sha,
            expected_artifact_sha256=expected_artifact_sha,
        )

    label_map_path = artifact_path_from_uri(str(model_record.get("label_map_uri") or ""))
    if label_map_path is None:
        return _status("label_map_invalid", detail="a local label_map_uri is required", model_id=model_id)
    if not label_map_path.exists():
        return _status("label_map_missing", detail=f"label map not found: {label_map_path}", model_id=model_id)

    expected_label_sha = str(model_record.get("label_map_sha256") or "").strip().lower()
    actual_label_sha = sha256_file(label_map_path).lower()
    if expected_label_sha and actual_label_sha != expected_label_sha:
        return _status(
            "label_map_checksum_mismatch",
            detail="label map checksum did not match registry",
            model_id=model_id,
            label_map_sha256=actual_label_sha,
            expected_label_map_sha256=expected_label_sha,
        )

    try:
        labels = _read_label_map(label_map_path)
    except Exception as exc:
        return _status("label_map_parse_failed", detail=str(exc), model_id=model_id)

    feature_params = _jsonish(model_record.get("feature_params")) or {}
    if not isinstance(feature_params, dict):
        feature_params = {}
    target_sr = int(model_record.get("input_sample_rate_hz") or sample_rate or 16000)
    if target_sr != int(sample_rate):
        return _status(
            "sample_rate_mismatch",
            detail="samples must be decoded/resampled before inference",
            model_id=model_id,
            sample_rate_hz=int(sample_rate),
            expected_sample_rate_hz=target_sr,
        )

    feature_bundle = extract_sine_feature_tensor(
        samples,
        sample_rate,
        window_sec=float(model_record.get("window_sec") or feature_params.get("window_sec") or 30.0),
        n_fft=int(feature_params.get("n_fft") or 1024),
        hop_length=int(feature_params.get("hop_length") or 320),
        n_mels=int(feature_params.get("n_mels") or 64),
        max_frames=int(feature_params["max_frames"]) if feature_params.get("max_frames") is not None else None,
    )
    tensor = feature_bundle["tensor"]

    backends = runtime_backend_status()
    runtime = _normalize_runtime(model_record.get("runtime") or model_record.get("framework"))
    started = time.perf_counter()
    try:
        if runtime in {"pytorch", "torch", "torchscript"}:
            if not backends["torch"]:
                return _status("model_runtime_unavailable", detail="torch is not installed", model_id=model_id)
            raw_output = _run_torchscript(artifact_path, tensor)
            runtime_name = "torchscript"
        elif runtime in {"onnx", "onnxruntime"}:
            if not backends["onnxruntime"]:
                return _status("model_runtime_unavailable", detail="onnxruntime is not installed", model_id=model_id)
            raw_output = _run_onnx(artifact_path, tensor)
            runtime_name = "onnxruntime"
        else:
            return _status("model_runtime_unsupported", detail=f"unsupported runtime: {runtime}", model_id=model_id)
    except Exception as exc:
        return _status("model_inference_failed", detail=str(exc), model_id=model_id)

    latency_ms = (time.perf_counter() - started) * 1000
    raw_scores, embedding = _output_parts(raw_output)
    probabilities = _stable_probabilities(raw_scores.reshape(-1))
    if probabilities.size != len(labels):
        return _status(
            "model_output_shape_mismatch",
            detail="model output size does not match label map",
            model_id=model_id,
            output_size=int(probabilities.size),
            label_count=len(labels),
        )
    ranked = _top_k_labels(probabilities, labels, top_k)
    if not ranked:
        return _status("model_output_empty", detail="model produced no labels", model_id=model_id)

    top = ranked[0]
    scores = {item["label"]: item["confidence"] for item in ranked}
    embedding_sha = vector_sha256(embedding) if embedding else None
    ood = _ood_metrics(
        probabilities,
        min_confidence=_float_param(model_record.get("min_confidence"), feature_params.get("min_confidence"), default=0.0),
        ood_threshold=_float_param(model_record.get("ood_threshold"), feature_params.get("ood_threshold"), default=1.0),
    )
    return {
        "ok": True,
        "status": "model_output_ready",
        "model_status": "model_ready",
        "model_id": model_id,
        "model_name": model_record.get("model_name"),
        "model_version": model_record.get("model_version"),
        "framework": model_record.get("framework"),
        "runtime": runtime_name,
        "output_kind": "classification",
        "top_label": top["label"],
        "confidence": top["confidence"],
        "confidence_margin": ood["confidence_margin"],
        "entropy": ood["entropy"],
        "normalized_entropy": ood["normalized_entropy"],
        "ood_score": ood["ood_score"],
        "ood_status": ood["ood_status"],
        "ood_threshold": ood["ood_threshold"],
        "min_confidence": ood["min_confidence"],
        "labels": ranked,
        "scores": scores,
        "embedding": embedding,
        "artifact_uri": str(artifact_path),
        "artifact_sha256": actual_artifact_sha,
        "label_map_uri": str(label_map_path),
        "label_map_sha256": actual_label_sha,
        "runtime_ms": latency_ms,
        "latency_ms": latency_ms,
        "embedding_sha256": embedding_sha,
        "embedding_dim": len(embedding),
        "feature_metadata": feature_bundle["metadata"],
        "feature_sha256": feature_bundle["metadata"]["feature_sha256"],
        "tensor_shape": feature_bundle["metadata"]["tensor_shape"],
        "sample_rate_hz": int(sample_rate),
        "window_sec": float(model_record.get("window_sec") or feature_params.get("window_sec") or 30.0),
    }
