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

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
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
