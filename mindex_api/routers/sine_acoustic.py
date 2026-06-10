"""
SINE acoustic intelligence API — library + analysis + Sonic Visualiser layers.

Powers https://mycosoft.com/sensing/sine and NatureOS MINDEX acoustic player.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from ..services.sine_acoustic.audio_io import load_mono
from ..services.sine_acoustic import run_full_analysis
from ..services.sine_acoustic.analysis_runner import run_and_persist_loaded_model_outputs
from ..services.sine_acoustic.classifier import classify_acoustic_file
from ..services.sine_acoustic.detector_registry import DETECTORS, DEFAULT_DETECTOR_IDS
from ..services.sine_acoustic.event_views import build_library_classification_payload
from ..services.sine_acoustic.model_runtime import inspect_sine_model_runtime
from ..services.sine_acoustic.persisted_evidence import list_persisted_analysis_evidence
from ..services.sine_acoustic.request_contract import read_sine_request_contract
from ..services.sine_acoustic.visualisation import build_visualisation_layers
from .library import _resolve_blob_path, list_blobs, list_sources, stream_blob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sine", tags=["sine"])


async def _seed_detectors(db: AsyncSession) -> None:
    for d in DETECTORS.values():
        await db.execute(
            text(
                """
                INSERT INTO library.detector (
                    id, name, category, upstream_project, upstream_url,
                    description, method, version, enabled
                ) VALUES (
                    :id, :name, :category, :upstream_project, :upstream_url,
                    :description, :method, '1.0', TRUE
                )
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    upstream_url = EXCLUDED.upstream_url,
                    description = EXCLUDED.description,
                    method = EXCLUDED.method,
                    updated_at = NOW()
                """
            ),
            {
                "id": d["id"],
                "name": d["name"],
                "category": d["category"],
                "upstream_project": d.get("upstream_project"),
                "upstream_url": d.get("upstream_url"),
                "description": d["description"],
                "method": d["method"],
            },
        )


@router.get("/status")
async def sine_status(db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """SINE stack health + blob counts."""
    try:
        await _seed_detectors(db)
        await db.commit()
        row = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*)::int AS blobs,
                           COUNT(DISTINCT origin_dataset_id)::int AS sources
                    FROM library.blob WHERE category = 'acoustic'
                    """
                )
            )
        ).mappings().first()
        det_count = (
            await db.execute(text("SELECT COUNT(*)::int FROM library.detector"))
        ).scalar() or 0
        model_context = await inspect_sine_model_runtime(db)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=f"sine_schema_missing: {exc!s}") from exc
    return {
        "status": "ok",
        "product": "SINE",
        "url": "https://mycosoft.com/sensing/sine",
        "acoustic_blobs": row["blobs"] if row else 0,
        "library_sources": row["sources"] if row else 0,
        "detectors_registered": det_count,
        "default_detectors": DEFAULT_DETECTOR_IDS,
        "model_status": model_context.get("model_status", "model_unavailable"),
        "model_ready": bool(model_context.get("model_ready", False)),
        "registered_models": int(model_context.get("registered_models", 0)),
        "loaded_models": int(model_context.get("loaded_models", 0)),
        "runtime_backends": model_context.get("runtime_backends") or {},
        "runtime_supported": bool(model_context.get("runtime_supported", False)),
        "inference_ready": bool(model_context.get("inference_ready", False)),
        "prototype_catalog_ready": bool(model_context.get("prototype_catalog_ready", False)),
        "blocking_reasons": model_context.get("blocking_reasons") or [],
    }


@router.get("/detectors")
async def sine_detectors(db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    await _seed_detectors(db)
    await db.commit()
    rows = (
        await db.execute(
            text(
                """
                SELECT id, name, category, upstream_project, upstream_url,
                       description, method, version, enabled, requires_gpu, metadata
                FROM library.detector
                ORDER BY name
                """
            )
        )
    ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _registry_unavailable(kind: str, *, detail: str | None = None) -> dict[str, Any]:
    if kind == "models":
        return {
            "ok": False,
            "status": "model_registry_unavailable",
            "model_status": "model_unavailable",
            "model_ready": False,
            "models": [],
            "registered_models": [],
            "loaded_models": [],
            "total": 0,
            "message": detail or "MINDEX SINE model registry has not been initialized yet.",
        }
    return {
        "ok": False,
        "status": "prototype_catalog_unavailable",
        "model_status": "model_unavailable",
        "prototype_ready": False,
        "prototypes": [],
        "prototype_catalog": [],
        "total": 0,
        "message": detail or "MINDEX SINE prototype catalog has not been initialized yet.",
    }


async def _regclass_exists(db: AsyncSession, relation: str) -> bool:
    try:
        return bool((await db.execute(text("SELECT to_regclass(:relation)"), {"relation": relation})).scalar())
    except Exception:
        await db.rollback()
        return False


@router.get("/models")
async def sine_models(db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """Registered SINE model artifacts and load state.

    This endpoint is intentionally honest: until the registry migration and a
    real artifact are present it returns model_unavailable, not detector
    readiness.
    """
    if not await _regclass_exists(db, "sine.model_artifact"):
        return _registry_unavailable("models")
    try:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT
                        id::text, model_id, model_name, model_version, domain,
                        target_domains, class_families, framework, runtime,
                        artifact_uri, artifact_sha256, label_map_uri, label_map_sha256,
                        training_dataset, metrics_uri, confusion_matrix_uri,
                        input_sample_rate_hz, window_sec, label_count, embedding_dim,
                        device, status, loaded, last_loaded_at, last_inference_at,
                        last_error, backend_commit, feature_params, created_at, updated_at
                    FROM sine.model_artifact
                    ORDER BY COALESCE(loaded, FALSE) DESC, updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                    """
                )
            )
        ).mappings().all()
    except Exception as exc:
        await db.rollback()
        return _registry_unavailable("models", detail=f"SINE model registry query failed: {exc!s}")

    models: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in ("target_domains", "class_families", "feature_params"):
            item[key] = _json_value(item.get(key))
        item["loaded"] = bool(item.get("loaded"))
        item["ready"] = bool(item["loaded"] and item.get("status") in {"model_ready", "ready", "loaded"})
        models.append(item)

    loaded = [item for item in models if item["loaded"]]
    model_ready = any(item["ready"] for item in models)
    return {
        "ok": model_ready,
        "status": "model_ready" if model_ready else "model_registry_empty" if not models else "model_unavailable",
        "model_status": "model_ready" if model_ready else "model_unavailable",
        "model_ready": model_ready,
        "models": models,
        "registered_models": models,
        "loaded_models": loaded,
        "total": len(models),
    }


@router.get("/models/{model_id}")
async def sine_model_detail(model_id: str, db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    if not await _regclass_exists(db, "sine.model_artifact"):
        raise HTTPException(status_code=404, detail="model_registry_unavailable")
    row = (
        await db.execute(
            text(
                """
                SELECT
                    id::text, model_id, model_name, model_version, domain,
                    target_domains, class_families, framework, runtime,
                    artifact_uri, artifact_sha256, label_map_uri, label_map_sha256,
                    training_dataset, metrics_uri, confusion_matrix_uri,
                    input_sample_rate_hz, window_sec, label_count, embedding_dim,
                    device, status, loaded, last_loaded_at, last_inference_at,
                    last_error, backend_commit, feature_params, created_at, updated_at
                FROM sine.model_artifact
                WHERE model_id = :model_id OR id::text = :model_id
                """
            ),
            {"model_id": model_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="model_not_found")
    item = dict(row)
    for key in ("target_domains", "class_families", "feature_params"):
        item[key] = _json_value(item.get(key))
    item["loaded"] = bool(item.get("loaded"))
    item["ready"] = bool(item["loaded"] and item.get("status") in {"model_ready", "ready", "loaded"})
    return item


@router.get("/prototypes")
async def sine_prototypes(
    domain: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    model_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Registered SINE acoustic prototypes/fingerprints."""
    if not await _regclass_exists(db, "sine.prototype"):
        return _registry_unavailable("prototypes")
    clauses = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if domain:
        clauses.append("domain = :domain")
        params["domain"] = domain
    if category:
        clauses.append("category = :category")
        params["category"] = category
    if model_id:
        clauses.append("model_id = :model_id")
        params["model_id"] = model_id
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        total = (
            await db.execute(text(f"SELECT COUNT(*)::int FROM sine.prototype {where}"), params)
        ).scalar() or 0
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT
                        id::text, prototype_id, label, domain, category, source,
                        source_uri, license, model_id, embedding_dim, vector_sha256,
                        prototype_sha256, example_count, metadata, created_at, updated_at
                    FROM sine.prototype
                    {where}
                    ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
        ).mappings().all()
    except Exception as exc:
        await db.rollback()
        return _registry_unavailable("prototypes", detail=f"SINE prototype catalog query failed: {exc!s}")

    prototypes: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = _json_value(item.get("metadata"))
        item["vector_checksum"] = item.get("vector_sha256") or item.get("prototype_sha256")
        prototypes.append(item)
    ready = bool(prototypes)
    return {
        "ok": ready,
        "status": "prototype_catalog_ready" if ready else "prototype_catalog_empty",
        "model_status": "model_unavailable",
        "prototype_ready": ready,
        "prototypes": prototypes,
        "prototype_catalog": prototypes,
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/library/sources")
async def sine_library_sources(
    category: str = Query("acoustic"),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await list_sources(category=category, db=db)


@router.get("/library/blobs")
async def sine_library_blobs(
    category: str = Query("acoustic"),
    sensor_type: Optional[str] = None,
    origin_dataset_id: Optional[str] = None,
    label_primary: Optional[str] = None,
    acoustic_environment: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    data = await list_blobs(
        category=category,
        sensor_type=sensor_type,
        origin_dataset_id=origin_dataset_id,
        label_primary=label_primary,
        acoustic_environment=acoustic_environment,
        q=q,
        limit=limit,
        offset=offset,
        db=db,
    )
    for item in data.get("items", []):
        item["stream_url"] = f"/api/mindex/sine/library/blobs/{item['id']}/stream"
        item["analyze_url"] = f"/api/mindex/sine/blobs/{item['id']}/analyze"
        item["visualisation_url"] = f"/api/mindex/sine/blobs/{item['id']}/visualisation"
    return data


@router.get("/library/blobs/{blob_id}/stream")
async def sine_stream_blob(blob_id: UUID, db: AsyncSession = Depends(get_db_session)):
    return await stream_blob(blob_id, db)


@router.get("/blobs/{blob_id}")
async def sine_get_blob(blob_id: UUID, db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    row = (
        await db.execute(text("SELECT * FROM library.blob WHERE id = :id"), {"id": blob_id})
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="blob_not_found")
    data = dict(row)
    data["id"] = str(data["id"])
    if data.get("manifest_id"):
        data["manifest_id"] = str(data["manifest_id"])
    data["stream_url"] = f"/api/mindex/sine/library/blobs/{data['id']}/stream"
    return data


@router.post("/blobs/{blob_id}/analyze")
async def sine_analyze_blob(
    request: Request,
    blob_id: UUID,
    detectors: Optional[str] = Query(None, description="Comma-separated detector ids"),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT id, abs_path, filename, label_primary FROM library.blob WHERE id = :id"
            ),
            {"id": blob_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="blob_not_found")
    path = _resolve_blob_path(row["abs_path"])
    det_list = [s.strip() for s in detectors.split(",") if s.strip()] if detectors else None
    request_contract = await read_sine_request_contract(request)
    model_context = await inspect_sine_model_runtime(db, request_contract=request_contract)

    run_id = (
        await db.execute(
            text(
                """
                INSERT INTO library.analysis_run (blob_id, status, detectors_requested)
                VALUES (:blob_id, 'running', CAST(:dets AS text[]))
                RETURNING id
                """
            ),
            {
                "blob_id": blob_id,
                "dets": det_list or DEFAULT_DETECTOR_IDS,
            },
        )
    ).scalar()

    try:
        result = await asyncio.to_thread(
            classify_acoustic_file,
            path,
            detectors=det_list,
            library_label=row.get("label_primary"),
            request_contract=request_contract,
            model_context=model_context,
        )
        for ev in result["events"]:
            await db.execute(
                text(
                    """
                    INSERT INTO library.detection_event (
                        analysis_run_id, blob_id, detector_id, label, confidence,
                        start_sec, end_sec, frequency_hz, metadata
                    ) VALUES (
                        :run_id, :blob_id, :det_id, :label, :conf,
                        :t0, :t1, :freq, CAST(:meta AS jsonb)
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "blob_id": blob_id,
                    "det_id": ev.get("detector_id", "unknown"),
                    "label": ev["label"],
                    "conf": ev.get("confidence"),
                    "t0": ev.get("start_sec"),
                    "t1": ev.get("end_sec"),
                    "freq": ev.get("frequency_hz"),
                    "meta": json.dumps(ev.get("metadata") or {}),
                },
            )
        model_inference = await run_and_persist_loaded_model_outputs(
            db,
            blob_id=blob_id,
            analysis_run_id=run_id,
            wav_path=path,
        )
        summary = {
            "detector_status": result["detector_status"],
            "event_count": len(result["events"]),
            "duration_sec": result["duration_sec"],
            "sample_rate_hz": result["sample_rate_hz"],
            "model_status": model_inference.get("model_status") or result.get("model_status", "model_unavailable"),
            "identification_status": result.get("identification_status", "detector_only"),
            "request_contract": request_contract,
            "model_context": model_context,
            "model_inference": model_inference,
        }
        await db.execute(
            text(
                """
                UPDATE library.analysis_run SET
                    status = 'complete',
                    finished_at = NOW(),
                    summary = CAST(:summary AS jsonb),
                    visualisation = CAST(:vis AS jsonb)
                WHERE id = :id
                """
            ),
            {
                "id": run_id,
                "summary": json.dumps(summary),
                "vis": json.dumps(result.get("visualisation") or {}),
            },
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await db.execute(
            text(
                """
                UPDATE library.analysis_run SET status = 'failed', finished_at = NOW(),
                    error_message = :err WHERE id = :id
                """
            ),
            {"id": run_id, "err": str(exc)},
        )
        await db.commit()
        raise HTTPException(status_code=500, detail=f"analysis_failed: {exc!s}") from exc

    events = (
        await db.execute(
            text(
                """
                SELECT detector_id, label, confidence, start_sec, end_sec,
                       frequency_hz, metadata
                FROM library.detection_event
                WHERE analysis_run_id = :run_id
                ORDER BY confidence DESC NULLS LAST
                """
            ),
            {"run_id": run_id},
        )
    ).mappings().all()

    run_row = (
        await db.execute(
            text(
                """
                SELECT summary, visualisation FROM library.analysis_run WHERE id = :id
                """
            ),
            {"id": run_id},
        )
    ).mappings().first()
    run_summary = dict(run_row["summary"]) if run_row and run_row.get("summary") else {}
    vis = run_row.get("visualisation") if run_row else None

    flat_events = [dict(e) for e in events]
    persisted_evidence = await list_persisted_analysis_evidence(db, run_id)
    classification = build_library_classification_payload(
        flat_events,
        summary=run_summary,
        visualisation=vis,
        analysis_run_id=str(run_id),
        request_contract=request_contract,
        model_context=model_context,
        **persisted_evidence,
    )
    return {
        "analysis_run_id": str(run_id),
        "blob_id": str(blob_id),
        "status": "complete",
        "summary": run_summary,
        "events": flat_events,
        "visualisation": classification.get("visualisation"),
        "classification": classification,
        **{k: classification[k] for k in (
            "frequency_detections",
            "activity_segments",
            "bird_detections",
            "uav_detections",
            "nps_detections",
            "deep_signal_detections",
            "deep_signal_matches",
            "model_status",
            "model_context",
            "model_outputs",
            "prototype_matches",
            "fusion_evidence",
            "sound_transcripts",
            "diagnostics",
            "detector_evidence",
            "identification_summary",
            "identification_status",
            "analysis_engine",
            "request_contract",
        ) if k in classification},
    }


@router.get("/blobs/{blob_id}/analysis")
async def sine_get_analysis(
    blob_id: UUID,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    run = (
        await db.execute(
            text(
                """
                SELECT id, status, detectors_requested, started_at, finished_at,
                       summary, visualisation, error_message
                FROM library.analysis_run
                WHERE blob_id = :blob_id
                ORDER BY started_at DESC
                LIMIT 1
                """
            ),
            {"blob_id": blob_id},
        )
    ).mappings().first()
    if not run:
        raise HTTPException(status_code=404, detail="no_analysis_run")
    run_id = run["id"]
    events = (
        await db.execute(
            text(
                """
                SELECT detector_id, label, confidence, start_sec, end_sec,
                       frequency_hz, metadata
                FROM library.detection_event
                WHERE analysis_run_id = :run_id
                """
            ),
            {"run_id": run_id},
        )
    ).mappings().all()
    data = dict(run)
    data["id"] = str(data["id"])
    flat_events = [dict(e) for e in events]
    summary = data.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    persisted_evidence = await list_persisted_analysis_evidence(db, run_id)
    classification = build_library_classification_payload(
        flat_events,
        summary=summary,
        visualisation=data.get("visualisation"),
        analysis_run_id=str(run_id),
        **persisted_evidence,
    )
    data["events"] = flat_events
    data["classification"] = classification
    data.update({
        k: classification[k]
        for k in (
            "frequency_detections",
            "activity_segments",
            "bird_detections",
            "uav_detections",
            "nps_detections",
            "deep_signal_detections",
            "deep_signal_matches",
            "model_status",
            "model_context",
            "model_outputs",
            "prototype_matches",
            "fusion_evidence",
            "sound_transcripts",
            "diagnostics",
            "detector_evidence",
            "identification_summary",
            "identification_status",
            "analysis_engine",
            "request_contract",
        )
        if k in classification
    })
    return data


@router.get("/training/human-tags")
async def sine_training_human_tags(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    training_eligible_only: bool = Query(True),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Training-eligible human identifications for active-learning export."""
    where = "WHERE training_eligible = TRUE" if training_eligible_only else ""
    try:
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT
                        h.id, h.blob_id, h.analysis_run_id, h.human_label, h.human_category,
                        h.human_confidence, h.human_notes, h.disputes_model, h.model_top_label,
                        h.model_confidence, h.model_summary, h.event_context, h.file_context,
                        h.review_status, h.training_eligible, h.created_by, h.created_at, h.updated_at,
                        b.filename, b.origin_dataset_id, b.label_primary, b.duration_sec
                    FROM library.acoustic_human_identification h
                    JOIN library.blob b ON b.id = h.blob_id
                    {where}
                    ORDER BY h.updated_at DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"limit": limit, "offset": offset},
            )
        ).mappings().all()
        total = (
            await db.execute(
                text(
                    f"""
                    SELECT COUNT(*)::int AS total
                    FROM library.acoustic_human_identification h
                    {where}
                    """
                )
            )
        ).scalar() or 0
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"human_tags_query_failed: {exc!s}"
        ) from exc

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in ("id", "blob_id", "analysis_run_id"):
            if item.get(key) is not None:
                item[key] = str(item[key])
        for key in ("model_summary", "event_context", "file_context"):
            val = item.get(key)
            if isinstance(val, str):
                try:
                    item[key] = json.loads(val)
                except json.JSONDecodeError:
                    pass
        for key in ("human_confidence", "model_confidence", "duration_sec"):
            if item.get(key) is not None:
                item[key] = float(item[key])
        items.append(item)
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


@router.get("/blobs/{blob_id}/visualisation")
async def sine_visualisation(
    blob_id: UUID,
    start_sec: float = Query(0.0, ge=0.0),
    end_sec: Optional[float] = Query(None, ge=0.0),
    max_waveform_points: int = Query(8192, ge=128, le=65536),
    waveform_points: Optional[int] = Query(None, ge=128, le=65536),
    max_time_frames: int = Query(1024, ge=16, le=4096),
    spec_time_bins: Optional[int] = Query(None, ge=16, le=4096),
    max_frequency_bins: int = Query(256, ge=16, le=2048),
    spec_freq_bins: Optional[int] = Query(None, ge=16, le=2048),
    fft_size: int = Query(2048, ge=64, le=16384),
    n_fft: Optional[int] = Query(None, ge=64, le=16384),
    hop_length: int = Query(128, ge=16, le=8192),
    window_function: str = Query("hann"),
    db_floor: float = Query(-96.0),
    db_ceiling: float = Query(0.0),
    include_peaks: bool = Query(True),
    quality: str = Query("oscilloscope"),
    ignore_saved_visualisation: bool = Query(False),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    if not ignore_saved_visualisation and quality != "oscilloscope":
        run = (
            await db.execute(
                text(
                    """
                    SELECT visualisation FROM library.analysis_run
                    WHERE blob_id = :blob_id AND visualisation IS NOT NULL
                    ORDER BY started_at DESC LIMIT 1
                    """
                ),
                {"blob_id": blob_id},
            )
        ).mappings().first()
        if run and run.get("visualisation"):
            return run["visualisation"]

    row = (
        await db.execute(
            text("SELECT abs_path, label_primary FROM library.blob WHERE id = :id"),
            {"id": blob_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="blob_not_found")
    path = _resolve_blob_path(row["abs_path"])
    try:
        samples, sample_rate = await asyncio.to_thread(load_mono, path)
        return await asyncio.to_thread(
            build_visualisation_layers,
            samples,
            sample_rate,
            start_sec=start_sec,
            end_sec=end_sec,
            waveform_points=waveform_points or max_waveform_points,
            spec_time_bins=spec_time_bins or max_time_frames,
            spec_freq_bins=spec_freq_bins or max_frequency_bins,
            fft_size=n_fft or fft_size,
            hop_length=hop_length,
            window_function=window_function,
            db_floor=db_floor,
            db_ceiling=db_ceiling,
            include_peaks=include_peaks,
            quality=quality,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"visualisation_failed: {exc!s}") from exc
