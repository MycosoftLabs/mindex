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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from ..services.sine_acoustic import run_full_analysis
from ..services.sine_acoustic.classifier import classify_acoustic_file
from ..services.sine_acoustic.detector_registry import DETECTORS, DEFAULT_DETECTOR_IDS
from ..services.sine_acoustic.event_views import build_library_classification_payload
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
        summary = {
            "detector_status": result["detector_status"],
            "event_count": len(result["events"]),
            "duration_sec": result["duration_sec"],
            "sample_rate_hz": result["sample_rate_hz"],
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
    classification = build_library_classification_payload(
        flat_events,
        summary=run_summary,
        visualisation=vis,
        analysis_run_id=str(run_id),
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
            "deep_signal_matches",
            "identification_summary",
            "identification_status",
            "analysis_engine",
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
    data["events"] = [dict(e) for e in events]
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
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
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
        result = await asyncio.to_thread(
            run_full_analysis,
            path,
            detectors=["visualisation_sonic"],
            library_label=row.get("label_primary"),
        )
        return result.get("visualisation") or {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"visualisation_failed: {exc!s}") from exc
