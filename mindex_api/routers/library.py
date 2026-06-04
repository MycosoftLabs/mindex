"""
MINDEX Library API — NAS-backed sensor blobs (Request 010/012).
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from ..services.library_annotations import (
    _json_value,
    _normalize_markers,
    _normalize_selection,
    _normalize_zoom,
    _parse_uuid,
    _require_acoustic_blob,
    _review_status,
    _row_to_human_identification,
    _row_to_wave_annotation,
)
from ..services.sine_acoustic.classifier import classify_acoustic_file
from ..services.sine_acoustic.detector_registry import DEFAULT_DETECTOR_IDS
from ..services.sine_acoustic.event_views import build_library_classification_payload

router = APIRouter(prefix="/library", tags=["library"])


def _nas_data_root() -> Path:
    try:
        from mindex_etl.library.nas_mount import nas_data_root

        return nas_data_root()
    except ImportError:
        raw = os.environ.get("NAS_MOUNT_PATH", "/mnt/nas/mindex").strip() or "/mnt/nas/mindex"
        return Path(raw)


def _library_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("MINDEX_LIBRARY_ROOT", "MINDEX_NAS_LIBRARY_ROOT", "NAS_LIBRARY_ROOT"):
        raw = os.environ.get(key, "").strip()
        if raw:
            roots.append(Path(raw))
    default = _nas_data_root() / "Library"
    if default not in roots:
        roots.append(default)
    return roots


def _resolve_blob_path(abs_path: str) -> Path:
    path = Path(abs_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file_not_found")
    resolved = path.resolve()
    nas_root = str(_nas_data_root().resolve())
    allowed = str(resolved).startswith(nas_root)
    if not allowed:
        for root in _library_roots():
            try:
                if str(resolved).startswith(str(root.resolve())):
                    allowed = True
                    break
            except OSError:
                continue
    if not allowed:
        raise HTTPException(status_code=403, detail="path_outside_library_root")
    return resolved


@router.get("/catalog")
async def library_catalog(
    limit: int = Query(100, ge=1, le=500),
    path: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Category counts from DB + optional directory listing."""
    counts: dict[str, int] = {}
    try:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT category, COUNT(*)::int AS n
                    FROM library.blob
                    GROUP BY category
                    """
                )
            )
        ).mappings().all()
        counts = {r["category"]: r["n"] for r in rows}
    except Exception:
        await db.rollback()

    items: list[dict[str, Any]] = []
    scanned: list[str] = []
    for root in _library_roots():
        if not root.is_dir():
            continue
        scanned.append(str(root))
        target = root / path if path else root / "acoustic"
        if not target.is_dir():
            target = root
        try:
            for entry in sorted(target.iterdir())[:limit]:
                if entry.name.startswith("."):
                    continue
                st = entry.stat()
                items.append(
                    {
                        "name": entry.name,
                        "path": str(entry.relative_to(root)).replace("\\", "/"),
                        "is_dir": entry.is_dir(),
                        "size_bytes": st.st_size if entry.is_file() else None,
                    }
                )
        except OSError:
            continue
        break

    return {
        "categories": counts,
        "items": items,
        "roots_scanned": scanned,
        "db_registered_total": sum(counts.values()),
        "mount_available": bool(scanned),
    }


@router.get("/storage")
async def library_storage() -> dict[str, Any]:
    """NAS mount health — library files must live on CIFS/NFS, not VM root disk."""
    try:
        from mindex_etl.library.nas_mount import is_remote_nas_mount, nas_usage_gb

        usage = nas_usage_gb()
        usage["library_acoustic"] = str(_nas_data_root() / "Library" / "acoustic")
        usage["policy"] = (
            "ok"
            if usage.get("remote_nas")
            else "vm_disk_only — mount //192.168.0.105/.../mindex at /mnt/nas/mindex"
        )
        return usage
    except Exception as exc:
        return {"available": False, "error": str(exc), "remote_nas": False}


@router.get("/sources")
async def list_sources(
    category: str = Query("acoustic"),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """NLM source catalog (parent datasets) with blob counts."""
    try:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT s.id, s.name, s.category, s.source_url, s.license,
                           s.nlm_subsystem, s.nlm_priority, s.sensor_type,
                           s.acoustic_environment, s.description, s.access_level, s.format,
                           COALESCE(b.cnt, 0)::int AS blob_count
                    FROM library.source s
                    LEFT JOIN (
                        SELECT origin_dataset_id, COUNT(*) AS cnt
                        FROM library.blob
                        WHERE category = :category
                        GROUP BY origin_dataset_id
                    ) b ON b.origin_dataset_id = s.id
                    WHERE s.category = :category
                    ORDER BY s.nlm_priority, s.name
                    """
                ),
                {"category": category},
            )
        ).mappings().all()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=f"library_source_missing: {exc!s}") from exc
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.get("/blobs")
async def list_blobs(
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
    clauses = ["category = :category"]
    params: dict[str, Any] = {"category": category, "lim": limit, "off": offset}
    if sensor_type:
        clauses.append("sensor_type = :sensor_type")
        params["sensor_type"] = sensor_type
    if origin_dataset_id:
        clauses.append("origin_dataset_id = :origin_dataset_id")
        params["origin_dataset_id"] = origin_dataset_id
    if label_primary:
        clauses.append("label_primary = :label_primary")
        params["label_primary"] = label_primary
    if acoustic_environment:
        clauses.append("acoustic_environment = :acoustic_environment")
        params["acoustic_environment"] = acoustic_environment
    if q:
        clauses.append(
            "(filename ILIKE :q OR source_id ILIKE :q OR title ILIKE :q "
            "OR description ILIKE :q OR label_primary ILIKE :q)"
        )
        params["q"] = f"%{q}%"
    where = " AND ".join(clauses)
    try:
        total = (
            await db.execute(
                text(f"SELECT COUNT(*)::int FROM library.blob WHERE {where}"),
                params,
            )
        ).scalar() or 0
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT id::text, source_id, origin_dataset_id, category, sensor_type,
                           title, description, label_primary, label_secondary,
                           acoustic_environment, source_name, source_url,
                           nlm_subsystem, nlm_priority, fold_id, training_split, locale,
                           filename, rel_path, size_bytes, duration_sec, sample_rate_hz,
                           channels, format, codec, playback_class, license,
                           needs_transcode, unsupported_codec, capture_time_utc, created_at
                    FROM library.blob
                    WHERE {where}
                    ORDER BY label_primary NULLS LAST, created_at DESC
                    LIMIT :lim OFFSET :off
                    """
                ),
                params,
            )
        ).mappings().all()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=f"library_schema_missing: {exc!s}") from exc

    items = []
    for r in rows:
        row = dict(r)
        row["stream_url"] = f"/api/mindex/library/blobs/{row['id']}/stream"
        items.append(row)
    return {"total": total, "items": items, "limit": limit, "offset": offset}


async def _latest_classification(
    blob_id: UUID, db: AsyncSession
) -> dict[str, Any] | None:
    run = (
        await db.execute(
            text(
                """
                SELECT id, summary, visualisation
                FROM library.analysis_run
                WHERE blob_id = :blob_id AND status = 'complete'
                ORDER BY finished_at DESC NULLS LAST, started_at DESC
                LIMIT 1
                """
            ),
            {"blob_id": blob_id},
        )
    ).mappings().first()
    if not run:
        return None
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
            {"run_id": run["id"]},
        )
    ).mappings().all()
    summary = run.get("summary")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except json.JSONDecodeError:
            summary = {}
    elif summary is None:
        summary = {}
    flat = [dict(e) for e in events]
    return build_library_classification_payload(
        flat,
        summary=summary if isinstance(summary, dict) else {},
        visualisation=run.get("visualisation"),
        analysis_run_id=str(run["id"]),
    )


async def _list_wave_annotations(blob_id: UUID, db: AsyncSession) -> list[dict[str, Any]]:
    try:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT id, blob_id, analysis_run_id, selection, zoom, markers,
                           loop_enabled, reverse_enabled, playback_rate, file_context,
                           created_by, created_at, updated_at
                    FROM library.acoustic_wave_annotation
                    WHERE blob_id = :blob_id
                    ORDER BY updated_at DESC
                    LIMIT 50
                    """
                ),
                {"blob_id": blob_id},
            )
        ).mappings().all()
    except Exception:
        await db.rollback()
        return []
    return [_row_to_wave_annotation(r) for r in rows]


async def _list_human_identifications(blob_id: UUID, db: AsyncSession) -> list[dict[str, Any]]:
    try:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT id, blob_id, analysis_run_id, human_label, human_category,
                           human_confidence, human_notes, disputes_model, model_top_label,
                           model_confidence, model_summary, event_context, file_context,
                           review_status, training_eligible, created_by, created_at, updated_at
                    FROM library.acoustic_human_identification
                    WHERE blob_id = :blob_id
                    ORDER BY created_at DESC
                    LIMIT 50
                    """
                ),
                {"blob_id": blob_id},
            )
        ).mappings().all()
    except Exception:
        await db.rollback()
        return []
    return [_row_to_human_identification(r) for r in rows]


@router.get("/blobs/{blob_id}")
async def get_blob(blob_id: UUID, db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    row = (
        await db.execute(
            text("SELECT * FROM library.blob WHERE id = :id"),
            {"id": blob_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="blob_not_found")
    data = dict(row)
    data["id"] = str(data["id"])
    if data.get("manifest_id"):
        data["manifest_id"] = str(data["manifest_id"])
    classification = await _latest_classification(blob_id, db)
    if classification:
        data.update(classification)
    data["stream_url"] = f"/api/mindex/library/blobs/{data['id']}/stream"
    data["wave_annotations"] = await _list_wave_annotations(blob_id, db)
    human_rows = await _list_human_identifications(blob_id, db)
    data["human_identifications"] = human_rows
    if human_rows:
        data["latest_human_identification"] = human_rows[0]
    return data


@router.post("/blobs/{blob_id}/classify")
async def classify_blob(
    blob_id: UUID,
    detectors: Optional[str] = Query(None, description="Comma-separated SINE detector ids"),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Run acoustic classifier (SINE only) and return Library-shaped detection groups."""
    row = (
        await db.execute(
            text(
                "SELECT id, abs_path, label_primary, category FROM library.blob WHERE id = :id"
            ),
            {"id": blob_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="blob_not_found")
    if row.get("category") != "acoustic":
        raise HTTPException(status_code=400, detail="classify_acoustic_only")
    path = _resolve_blob_path(row["abs_path"])
    det_list = [s.strip() for s in detectors.split(",") if s.strip()] if detectors else None
    try:
        payload = await asyncio.to_thread(
            classify_acoustic_file,
            path,
            detectors=det_list,
            library_label=row.get("label_primary"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"classify_failed: {exc!s}") from exc
    return {
        "blob_id": str(blob_id),
        "status": "complete",
        "detector_status": payload.get("detector_status"),
        "classification": payload,
        **{
            k: payload[k]
            for k in (
                "frequency_detections",
                "activity_segments",
                "bird_detections",
                "uav_detections",
                "nps_detections",
                "deep_signal_matches",
                "identification_summary",
                "identification_status",
                "analysis_engine",
                "visualisation",
            )
            if k in payload
        },
        "detectors_used": det_list or DEFAULT_DETECTOR_IDS,
    }


@router.post("/blobs/{blob_id}/wave-annotation")
async def create_wave_annotation(
    blob_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Persist SINE player wave region, zoom, and markers."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_json") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")

    blob = await _require_acoustic_blob(blob_id, db)
    duration = float(blob["duration_sec"]) if blob.get("duration_sec") is not None else None
    selection = _normalize_selection(body.get("selection"), duration)
    zoom = _normalize_zoom(body.get("zoom"), duration)
    markers = _normalize_markers(body.get("markers"), duration)
    if not selection and not markers:
        raise HTTPException(status_code=400, detail="selection_or_markers_required")

    analysis_run_id = _parse_uuid(body.get("analysis_run_id"))
    loop_enabled = bool(selection.get("loop_enabled")) if selection else False
    reverse_enabled = bool(selection.get("reverse_enabled")) if selection else False
    playback_rate = float(selection.get("playback_rate", 1)) if selection else 1.0
    file_context = body.get("file_context") if isinstance(body.get("file_context"), dict) else None
    created_by = body.get("created_by") if isinstance(body.get("created_by"), str) else None

    try:
        row = (
            await db.execute(
                text(
                    """
                    INSERT INTO library.acoustic_wave_annotation (
                        blob_id, analysis_run_id, selection, zoom, markers,
                        loop_enabled, reverse_enabled, playback_rate, file_context, created_by
                    ) VALUES (
                        :blob_id, :analysis_run_id,
                        CAST(:selection AS jsonb), CAST(:zoom AS jsonb), CAST(:markers AS jsonb),
                        :loop_enabled, :reverse_enabled, :playback_rate,
                        CAST(:file_context AS jsonb), :created_by
                    )
                    RETURNING id, blob_id, analysis_run_id, selection, zoom, markers,
                              loop_enabled, reverse_enabled, playback_rate, file_context,
                              created_by, created_at, updated_at
                    """
                ),
                {
                    "blob_id": blob_id,
                    "analysis_run_id": analysis_run_id,
                    "selection": _json_value(selection),
                    "zoom": _json_value(zoom),
                    "markers": _json_value(markers),
                    "loop_enabled": loop_enabled,
                    "reverse_enabled": reverse_enabled,
                    "playback_rate": playback_rate,
                    "file_context": _json_value(file_context),
                    "created_by": created_by,
                },
            )
        ).mappings().first()
        await db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=f"wave_annotation_save_failed: {exc!s}") from exc

    if not row:
        raise HTTPException(status_code=500, detail="wave_annotation_not_saved")
    return {"status": "saved", "annotation": _row_to_wave_annotation(row)}


@router.get("/blobs/{blob_id}/wave-annotations")
async def list_wave_annotations(
    blob_id: UUID, db: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    await _require_acoustic_blob(blob_id, db)
    items = await _list_wave_annotations(blob_id, db)
    return {"blob_id": str(blob_id), "items": items, "total": len(items)}


@router.post("/blobs/{blob_id}/human-identification")
async def create_human_identification(
    blob_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Store human sound label alongside model output (never overwrites model summary)."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_json") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")

    await _require_acoustic_blob(blob_id, db)
    human_label = str(body.get("human_label") or "").strip()
    if not human_label:
        raise HTTPException(status_code=400, detail="human_label_required")

    analysis_run_id = _parse_uuid(body.get("analysis_run_id"))
    human_category = str(body.get("human_category") or "").strip() or None
    human_notes = str(body.get("human_notes") or "").strip() or None
    disputes_model = bool(body.get("disputes_model", True))
    model_top_label = str(body.get("model_top_label") or "").strip() or None
    model_confidence = body.get("model_confidence")
    try:
        model_confidence_f = float(model_confidence) if model_confidence is not None else None
    except (TypeError, ValueError):
        model_confidence_f = None
    try:
        human_confidence_f = float(body.get("human_confidence")) if body.get("human_confidence") is not None else None
    except (TypeError, ValueError):
        human_confidence_f = None

    model_summary = body.get("model_summary") if isinstance(body.get("model_summary"), dict) else None
    event_context: dict[str, Any] = {}
    if body.get("current_time_sec") is not None:
        event_context["current_time_sec"] = body.get("current_time_sec")
    file_context = body.get("file_context") if isinstance(body.get("file_context"), dict) else None
    created_by = body.get("created_by") if isinstance(body.get("created_by"), str) else None
    review_status = _review_status(human_label, model_top_label, disputes_model)

    try:
        row = (
            await db.execute(
                text(
                    """
                    INSERT INTO library.acoustic_human_identification (
                        blob_id, analysis_run_id, human_label, human_category, human_confidence,
                        human_notes, disputes_model, model_top_label, model_confidence, model_summary,
                        event_context, file_context, review_status, created_by
                    ) VALUES (
                        :blob_id, :analysis_run_id, :human_label, :human_category, :human_confidence,
                        :human_notes, :disputes_model, :model_top_label, :model_confidence,
                        CAST(:model_summary AS jsonb), CAST(:event_context AS jsonb),
                        CAST(:file_context AS jsonb), :review_status, :created_by
                    )
                    RETURNING id, blob_id, analysis_run_id, human_label, human_category,
                              human_confidence, human_notes, disputes_model, model_top_label,
                              model_confidence, model_summary, event_context, file_context,
                              review_status, training_eligible, created_by, created_at, updated_at
                    """
                ),
                {
                    "blob_id": blob_id,
                    "analysis_run_id": analysis_run_id,
                    "human_label": human_label,
                    "human_category": human_category,
                    "human_confidence": human_confidence_f,
                    "human_notes": human_notes,
                    "disputes_model": disputes_model,
                    "model_top_label": model_top_label,
                    "model_confidence": model_confidence_f,
                    "model_summary": _json_value(model_summary),
                    "event_context": _json_value(event_context or None),
                    "file_context": _json_value(file_context),
                    "review_status": review_status,
                    "created_by": created_by,
                },
            )
        ).mappings().first()
        await db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503, detail=f"human_identification_save_failed: {exc!s}"
        ) from exc

    if not row:
        raise HTTPException(status_code=500, detail="human_identification_not_saved")
    return {"status": "saved", "identification": _row_to_human_identification(row)}


@router.get("/blobs/{blob_id}/human-identifications")
async def list_human_identifications(
    blob_id: UUID, db: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    await _require_acoustic_blob(blob_id, db)
    items = await _list_human_identifications(blob_id, db)
    return {"blob_id": str(blob_id), "items": items, "total": len(items)}


@router.get("/blobs/{blob_id}/stream")
async def stream_blob(blob_id: UUID, db: AsyncSession = Depends(get_db_session)):
    row = (
        await db.execute(
            text("SELECT abs_path, filename, format FROM library.blob WHERE id = :id"),
            {"id": blob_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="blob_not_found")
    path = _resolve_blob_path(row["abs_path"])
    media_type = mimetypes.guess_type(row["filename"] or str(path))[0] or "audio/wav"
    return FileResponse(path, media_type=media_type, filename=row["filename"])


@router.post("/import")
async def trigger_import(
    sources: str = Query("esc50,ds3500,mbari_pacific_sound"),
    max_files_per_source: int = Query(5000, ge=1, le=500000),
    max_gb: float = Query(200.0, ge=1, le=8000),
) -> dict[str, Any]:
    """Queue acoustic ingest (runs in ETL container / background)."""
    import subprocess

    cmd = [
        "python",
        "-m",
        "mindex_etl.jobs.ingest_nlm_audio_p0",
        "--sources",
        sources,
        "--max-files-per-source",
        str(max_files_per_source),
        "--max-gb",
        str(max_gb),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"status": "started", "pid": proc.pid, "command": " ".join(cmd)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"import_start_failed: {exc!s}") from exc
