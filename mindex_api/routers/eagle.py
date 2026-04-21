"""
Eagle Eye — MINDEX canonical API for video intelligence (Apr 17, 2026)

Schema: eagle.video_sources, eagle.video_events, eagle.object_tracks, eagle.scene_index
See migrations/eagle_schema_APR20_2026.sql and optional eagle_schema_privacy_APR17_2026.sql
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/eagle", tags=["Eagle Eye"])


class VideoSourceRow(BaseModel):
    id: str
    kind: str
    provider: str
    stable_location: bool
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_confidence: Optional[float] = None
    stream_url: Optional[str] = None
    embed_url: Optional[str] = None
    media_url: Optional[str] = None
    source_status: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None
    retention_policy: Optional[Dict[str, Any]] = None
    provenance_method: Optional[str] = None
    privacy_class: Optional[str] = None
    updated_at: Optional[str] = None


class VideoSourceUpsert(BaseModel):
    id: str
    kind: str = "permanent"
    provider: str
    stable_location: bool = True
    lat: Optional[float] = None
    lng: Optional[float] = None
    location_confidence: Optional[float] = None
    stream_url: Optional[str] = None
    embed_url: Optional[str] = None
    media_url: Optional[str] = None
    source_status: Optional[str] = "active"
    permissions: Optional[Dict[str, Any]] = None
    retention_policy: Optional[Dict[str, Any]] = None
    provenance_method: Optional[str] = None
    privacy_class: Optional[str] = None


class VideoEventRow(BaseModel):
    id: str
    video_source_id: Optional[str] = None
    observed_at: str
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    native_place: Optional[str] = None
    inferred_place: Optional[str] = None
    inference_confidence: Optional[float] = None
    text_context: Optional[str] = None
    thumbnail_url: Optional[str] = None
    clip_ref: Optional[str] = None
    raw_metadata: Optional[Dict[str, Any]] = None


class BulkSourcesRequest(BaseModel):
    sources: List[VideoSourceUpsert] = Field(default_factory=list)


class BulkEventsRequest(BaseModel):
    events: List[VideoEventRow] = Field(default_factory=list)


async def _safe_exec(session: AsyncSession, sql: str, params: dict, label: str) -> None:
    try:
        await session.execute(text(sql), params)
    except Exception as e:
        logger.error("Eagle %s: %s", label, e)
        raise HTTPException(status_code=500, detail=f"eagle_{label}: {e!s}") from e


@router.get("/health/stats")
async def eagle_stats(session: AsyncSession = Depends(get_db_session)):
    """Row counts for observability (MAS / ops dashboards)."""
    out: Dict[str, Any] = {"schema": "eagle"}
    for table in ("video_sources", "video_events", "object_tracks", "scene_index"):
        try:
            r = await session.execute(text(f"SELECT count(*) FROM eagle.{table}"))
            out[table] = int(r.scalar_one())
        except Exception as e:
            out[table] = None
            out[f"{table}_error"] = str(e)[:200]
    return out


@router.get("/video-sources", response_model=Dict[str, Any])
async def list_video_sources(
    lat_min: float = Query(...),
    lat_max: float = Query(...),
    lng_min: float = Query(...),
    lng_max: float = Query(...),
    kind: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    limit: int = Query(2000, ge=1, le=20000),
    session: AsyncSession = Depends(get_db_session),
):
    """Bbox query for permanent camera rows (same bounds as /earth/map/bbox)."""
    where = [
        "lat IS NOT NULL AND lng IS NOT NULL",
        "lat BETWEEN :lat_min AND :lat_max",
        "lng BETWEEN :lng_min AND :lng_max",
    ]
    params: Dict[str, Any] = {
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lng_min": lng_min,
        "lng_max": lng_max,
        "limit": limit,
    }
    if kind:
        where.append("kind = :kind")
        params["kind"] = kind
    if provider:
        where.append("provider = :provider")
        params["provider"] = provider

    sql = f"""
        SELECT id, kind, provider, stable_location, lat, lng, location_confidence,
               stream_url, embed_url, media_url, source_status, permissions, retention_policy,
               updated_at::text
        FROM eagle.video_sources
        WHERE {' AND '.join(where)}
        ORDER BY updated_at DESC NULLS LAST
        LIMIT :limit
    """
    try:
        result = await session.execute(text(sql), params)
        rows = result.fetchall()
    except Exception as e:
        if "does not exist" in str(e):
            return {"sources": [], "total": 0, "note": "eagle.video_sources not migrated"}
        raise HTTPException(status_code=500, detail=str(e)) from e

    sources = [dict(r._mapping) for r in rows]
    return {"sources": sources, "total": len(sources)}


@router.get("/video-events", response_model=Dict[str, Any])
async def list_video_events(
    observed_after: Optional[str] = Query(None, description="ISO8601"),
    observed_before: Optional[str] = Query(None, description="ISO8601"),
    video_source_id: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=10000),
    session: AsyncSession = Depends(get_db_session),
):
    """Time-range query for ephemeral / social clip rows."""
    where = ["1=1"]
    params: Dict[str, Any] = {"limit": limit}
    if observed_after:
        where.append("observed_at >= :observed_after::timestamptz")
        params["observed_after"] = observed_after
    if observed_before:
        where.append("observed_at <= :observed_before::timestamptz")
        params["observed_before"] = observed_before
    if video_source_id:
        where.append("video_source_id = :video_source_id")
        params["video_source_id"] = video_source_id

    sql = f"""
        SELECT id, video_source_id, observed_at::text, start_at::text, end_at::text,
               native_place, inferred_place, inference_confidence, text_context,
               thumbnail_url, clip_ref, raw_metadata
        FROM eagle.video_events
        WHERE {' AND '.join(where)}
        ORDER BY observed_at DESC
        LIMIT :limit
    """
    try:
        result = await session.execute(text(sql), params)
        rows = result.fetchall()
    except Exception as e:
        if "does not exist" in str(e):
            return {"events": [], "total": 0, "note": "eagle.video_events not migrated"}
        raise HTTPException(status_code=500, detail=str(e)) from e

    events = [dict(r._mapping) for r in rows]
    return {"events": events, "total": len(events)}


@router.post("/video-sources/bulk-upsert")
async def bulk_upsert_sources(
    body: BulkSourcesRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """Internal: website connectors / MAS jobs persist normalized rows."""
    if not body.sources:
        return {"upserted": 0, "errors": 0}

    # Optional columns (migration eagle_schema_privacy_APR17_2026.sql)
    upsert_sql = """
        INSERT INTO eagle.video_sources (
            id, kind, provider, stable_location, lat, lng, location_confidence,
            stream_url, embed_url, media_url, source_status, permissions, retention_policy,
            provenance_method, privacy_class, updated_at
        ) VALUES (
            :id, :kind, :provider, :stable_location, :lat, :lng, :location_confidence,
            :stream_url, :embed_url, :media_url, :source_status,
            CAST(:permissions AS jsonb), CAST(:retention_policy AS jsonb),
            :provenance_method, :privacy_class, NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            kind = EXCLUDED.kind,
            provider = EXCLUDED.provider,
            stable_location = EXCLUDED.stable_location,
            lat = EXCLUDED.lat,
            lng = EXCLUDED.lng,
            location_confidence = EXCLUDED.location_confidence,
            stream_url = EXCLUDED.stream_url,
            embed_url = EXCLUDED.embed_url,
            media_url = EXCLUDED.media_url,
            source_status = EXCLUDED.source_status,
            permissions = EXCLUDED.permissions,
            retention_policy = EXCLUDED.retention_policy,
            provenance_method = COALESCE(EXCLUDED.provenance_method, eagle.video_sources.provenance_method),
            privacy_class = COALESCE(EXCLUDED.privacy_class, eagle.video_sources.privacy_class),
            updated_at = NOW()
    """

    upsert_sql_legacy = """
        INSERT INTO eagle.video_sources (
            id, kind, provider, stable_location, lat, lng, location_confidence,
            stream_url, embed_url, media_url, source_status, permissions, retention_policy,
            updated_at
        ) VALUES (
            :id, :kind, :provider, :stable_location, :lat, :lng, :location_confidence,
            :stream_url, :embed_url, :media_url, :source_status,
            CAST(:permissions AS jsonb), CAST(:retention_policy AS jsonb),
            NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            kind = EXCLUDED.kind,
            provider = EXCLUDED.provider,
            stable_location = EXCLUDED.stable_location,
            lat = EXCLUDED.lat,
            lng = EXCLUDED.lng,
            location_confidence = EXCLUDED.location_confidence,
            stream_url = EXCLUDED.stream_url,
            embed_url = EXCLUDED.embed_url,
            media_url = EXCLUDED.media_url,
            source_status = EXCLUDED.source_status,
            permissions = EXCLUDED.permissions,
            retention_policy = EXCLUDED.retention_policy,
            updated_at = NOW()
    """

    upserted = 0
    errors = 0
    for s in body.sources:
        params = {
            "id": s.id,
            "kind": s.kind,
            "provider": s.provider,
            "stable_location": s.stable_location,
            "lat": s.lat,
            "lng": s.lng,
            "location_confidence": s.location_confidence,
            "stream_url": s.stream_url,
            "embed_url": s.embed_url,
            "media_url": s.media_url,
            "source_status": s.source_status or "active",
            "permissions": json.dumps(s.permissions or {}),
            "retention_policy": json.dumps(s.retention_policy or {}),
            "provenance_method": s.provenance_method,
            "privacy_class": s.privacy_class,
        }
        try:
            await session.execute(text(upsert_sql), params)
            upserted += 1
        except Exception as e:
            err = str(e)
            if "provenance_method" in err or "privacy_class" in err or "column" in err.lower():
                try:
                    await session.execute(text(upsert_sql_legacy), params)
                    upserted += 1
                except Exception as e2:
                    errors += 1
                    if errors <= 3:
                        logger.warning("Eagle bulk upsert legacy: %s", e2)
            else:
                errors += 1
                if errors <= 3:
                    logger.warning("Eagle bulk upsert: %s", e)

    if upserted:
        await session.commit()
    return {"upserted": upserted, "errors": errors}


@router.post("/video-events/bulk-upsert")
async def bulk_upsert_events(
    body: BulkEventsRequest,
    session: AsyncSession = Depends(get_db_session),
):
    if not body.events:
        return {"upserted": 0, "errors": 0}

    sql = """
        INSERT INTO eagle.video_events (
            id, video_source_id, observed_at, start_at, end_at,
            native_place, inferred_place, inference_confidence, text_context,
            thumbnail_url, clip_ref, raw_metadata
        ) VALUES (
            :id, :video_source_id, :observed_at::timestamptz, :start_at::timestamptz, :end_at::timestamptz,
            :native_place, :inferred_place, :inference_confidence, :text_context,
            :thumbnail_url, :clip_ref, CAST(:raw_metadata AS jsonb)
        )
        ON CONFLICT (id) DO UPDATE SET
            video_source_id = EXCLUDED.video_source_id,
            observed_at = EXCLUDED.observed_at,
            thumbnail_url = EXCLUDED.thumbnail_url,
            clip_ref = EXCLUDED.clip_ref,
            raw_metadata = EXCLUDED.raw_metadata
    """

    upserted = 0
    errors = 0
    for e in body.events:
        params = {
            "id": e.id,
            "video_source_id": e.video_source_id,
            "observed_at": e.observed_at,
            "start_at": e.start_at,
            "end_at": e.end_at,
            "native_place": e.native_place,
            "inferred_place": e.inferred_place,
            "inference_confidence": e.inference_confidence,
            "text_context": e.text_context,
            "thumbnail_url": e.thumbnail_url,
            "clip_ref": e.clip_ref,
            "raw_metadata": json.dumps(e.raw_metadata or {}),
        }
        try:
            await session.execute(text(sql), params)
            upserted += 1
        except Exception as ex:
            errors += 1
            if errors <= 3:
                logger.warning("Eagle event upsert: %s", ex)

    if upserted:
        await session.commit()
    return {"upserted": upserted, "errors": errors}


@router.get("/scene-index/search-text")
async def search_scene_text(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=100),
    privacy_public_only: bool = Query(True, description="If true, only rows linked to public-safe events/sources"),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Full-text style search over scene_index summaries (no embedding required).
    For vector similarity, use NLM batch pipeline into embedding column first.
    """
    # Join video_events for optional privacy filter when columns exist
    sql = """
        SELECT si.id, si.video_event_id, si.transcript, si.ocr_text, si.vlm_summary
        FROM eagle.scene_index si
        WHERE (
            si.vlm_summary ILIKE :q OR si.transcript ILIKE :q OR si.ocr_text ILIKE :q
        )
        ORDER BY si.id DESC
        LIMIT :limit
    """
    try:
        result = await session.execute(text(sql), {"q": f"%{q}%", "limit": limit})
        rows = result.fetchall()
    except Exception as e:
        if "does not exist" in str(e):
            return {"results": [], "total": 0}
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "results": [dict(r._mapping) for r in rows],
        "total": len(rows),
        "privacy_filter": privacy_public_only,
    }
