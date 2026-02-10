from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import (
    PaginationParams,
    get_db_session,
    pagination_params,
    require_api_key,
)
from ..contracts.v1.telemetry import DeviceLatestSamplesResponse, DeviceListResponse
from ..schemas.telemetry import (
    DeviceHealthState,
    DeviceHealthStateCreate,
    EnvelopeIngestRequest,
    EnvelopeIngestResponse,
    ReplayStartRequest,
    ReplayState,
    ReplayUpdateRequest,
    TelemetrySampleRow,
)

telemetry_router = APIRouter(
    prefix="/telemetry",
    tags=["telemetry"],
    dependencies=[Depends(require_api_key)],
)

devices_router = APIRouter(
    prefix="/devices",
    tags=["devices"],
    dependencies=[Depends(require_api_key)],
)


def _dedupe_key(device_slug: str, envelope_seq: int, envelope_msg_id: str, reading_id: str) -> str:
    return f"{device_slug}:{envelope_seq}:{envelope_msg_id}:{reading_id}"


@telemetry_router.get("/devices/latest", response_model=DeviceLatestSamplesResponse)
async def get_device_latest_samples(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
) -> DeviceLatestSamplesResponse:
    stmt = text(
        """
        SELECT
            device_id,
            device_name,
            device_slug,
            stream_id,
            stream_key,
            stream_unit,
            sample_id,
            recorded_at,
            value_numeric,
            value_text,
            value_json,
            value_unit,
            sample_metadata,
            ST_AsGeoJSON(sample_location::geometry) AS sample_location_geojson,
            ST_AsGeoJSON(device_location::geometry) AS device_location_geojson
        FROM app.v_device_latest_samples
        ORDER BY recorded_at DESC NULLS LAST
        LIMIT :limit OFFSET :offset
        """
    )
    result = await db.execute(
        stmt,
        {
            "limit": pagination.limit,
            "offset": pagination.offset,
        },
    )
    rows = []
    for row in result.mappings().all():
        data = dict(row)
        sample_loc = data.pop("sample_location_geojson", None)
        device_loc = data.pop("device_location_geojson", None)
        if sample_loc:
            data["sample_location"] = json.loads(sample_loc)
        else:
            data["sample_location"] = None
        if device_loc:
            data["device_location"] = json.loads(device_loc)
        else:
            data["device_location"] = None
        rows.append(data)

    return DeviceLatestSamplesResponse(
        data=rows,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": None,
        },
    )


@devices_router.get("", response_model=DeviceListResponse)
async def list_devices(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    status_filter: Optional[str] = None,
) -> DeviceListResponse:
    # Build dynamic WHERE clause to avoid asyncpg NULL parameter issues
    where_clauses = []
    params: dict = {
        "limit": pagination.limit,
        "offset": pagination.offset,
    }

    if status_filter:
        where_clauses.append("status = :status")
        params["status"] = status_filter

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    stmt = text(
        f"""
        SELECT
            id,
            name,
            slug,
            status,
            taxon_id,
            metadata,
            created_at,
            updated_at,
            ST_AsGeoJSON(location::geometry) AS location_geojson
        FROM telemetry.device
        WHERE {where_sql}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
        """
    )
    count_stmt = text(
        f"""
        SELECT count(*)
        FROM telemetry.device
        WHERE {where_sql}
        """
    )

    result = await db.execute(stmt, params)
    count_result = await db.execute(count_stmt, params)
    total = count_result.scalar_one()

    devices = []
    for row in result.mappings().all():
        data = dict(row)
        loc = data.pop("location_geojson", None)
        data["location"] = json.loads(loc) if loc else None
        devices.append(data)

    return DeviceListResponse(
        data=devices,
        pagination={
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        },
    )


@telemetry_router.post("/envelope", response_model=EnvelopeIngestResponse)
async def ingest_envelope(
    request: EnvelopeIngestRequest,
    db: AsyncSession = Depends(get_db_session),
) -> EnvelopeIngestResponse:
    env = request.envelope or {}
    hdr = env.get("hdr") if isinstance(env, dict) else None
    ts = env.get("ts") if isinstance(env, dict) else None
    pack = env.get("pack") if isinstance(env, dict) else None

    if not isinstance(hdr, dict) or not isinstance(pack, list):
        raise HTTPException(status_code=400, detail="invalid_envelope_missing_hdr_pack")

    device_slug = str(hdr.get("deviceId") or "")
    envelope_msg_id = str(hdr.get("msgId") or "")
    envelope_seq = env.get("seq")
    if not device_slug or not envelope_msg_id or not isinstance(envelope_seq, int):
        raise HTTPException(status_code=400, detail="invalid_envelope_missing_deviceId_msgId_seq")

    # Resolve recorded_at (device timestamp if present, else now)
    recorded_at = None
    if isinstance(ts, dict):
        utc = ts.get("utc")
        if isinstance(utc, str):
            try:
                # Accept ISO-8601 strings
                from datetime import datetime as _dt

                recorded_at = _dt.fromisoformat(utc.replace("Z", "+00:00"))
            except Exception:
                recorded_at = None
    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc)

    # Resolve telemetry.device by slug, create if missing.
    device_row = await db.execute(
        text("SELECT id FROM telemetry.device WHERE slug = :slug LIMIT 1"),
        {"slug": device_slug},
    )
    device_id = device_row.scalar_one_or_none()
    if not device_id:
        ins = await db.execute(
            text(
                """
                INSERT INTO telemetry.device (name, slug, status, metadata)
                VALUES (:name, :slug, 'online', :metadata::jsonb)
                RETURNING id
                """
            ),
            {"name": device_slug, "slug": device_slug, "metadata": json.dumps({"source": "envelope"})},
        )
        device_id = ins.scalar_one()

    # Verification metadata (if upstream validated)
    verification = env.get("verification") if isinstance(env, dict) else None
    verified = bool(isinstance(verification, dict) and verification.get("hashValid") is True)
    verification_method = "envelope_hash"
    verification_metadata = verification if isinstance(verification, dict) else {}

    inserted = 0
    deduped = 0

    for reading in pack:
        if not isinstance(reading, dict):
            continue
        reading_id = str(reading.get("id") or "")
        if not reading_id:
            continue

        v = reading.get("v")
        unit = reading.get("u")
        value_numeric = v if isinstance(v, (int, float)) else None
        value_json = None if value_numeric is not None else (v if isinstance(v, (dict, list, str, bool)) else None)

        # Ensure stream exists
        stream_res = await db.execute(
            text(
                """
                INSERT INTO telemetry.stream (device_id, key, unit, metadata)
                VALUES (:device_id, :key, :unit, :metadata::jsonb)
                ON CONFLICT (device_id, key) DO UPDATE SET updated_at = now()
                RETURNING id
                """
            ),
            {
                "device_id": str(device_id),
                "key": reading_id,
                "unit": str(unit) if unit is not None else None,
                "metadata": json.dumps({"source": "envelope"}),
            },
        )
        stream_id = stream_res.scalar_one()

        dk = _dedupe_key(device_slug, envelope_seq, envelope_msg_id, reading_id)
        sample_res = await db.execute(
            text(
                """
                INSERT INTO telemetry.sample (
                    stream_id,
                    recorded_at,
                    value_numeric,
                    value_json,
                    value_unit,
                    metadata,
                    verified,
                    verified_at,
                    verified_by,
                    verification_method,
                    verification_metadata,
                    envelope_msg_id,
                    envelope_seq,
                    envelope_hash,
                    envelope_sig,
                    dedupe_key
                ) VALUES (
                    :stream_id,
                    :recorded_at,
                    :value_numeric,
                    :value_json::jsonb,
                    :value_unit,
                    :metadata::jsonb,
                    :verified,
                    CASE WHEN :verified THEN now() ELSE NULL END,
                    :verified_by,
                    :verification_method,
                    :verification_metadata::jsonb,
                    :envelope_msg_id,
                    :envelope_seq,
                    :envelope_hash,
                    :envelope_sig,
                    :dedupe_key
                )
                ON CONFLICT (dedupe_key) DO NOTHING
                """
            ),
            {
                "stream_id": str(stream_id),
                "recorded_at": recorded_at,
                "value_numeric": value_numeric,
                "value_json": json.dumps(value_json) if value_json is not None else None,
                "value_unit": str(unit) if unit is not None else None,
                "metadata": json.dumps({"deviceSlug": device_slug}),
                "verified": verified,
                "verified_by": request.verified_by,
                "verification_method": verification_method,
                "verification_metadata": json.dumps(verification_metadata),
                "envelope_msg_id": envelope_msg_id,
                "envelope_seq": envelope_seq,
                "envelope_hash": str(env.get("hash")) if isinstance(env, dict) else None,
                "envelope_sig": str(env.get("sig")) if isinstance(env, dict) else None,
                "dedupe_key": dk,
            },
        )
        if sample_res.rowcount and sample_res.rowcount > 0:
            inserted += 1
        else:
            deduped += 1

    await db.commit()

    return EnvelopeIngestResponse(
        success=True,
        device_slug=device_slug,
        envelope_msg_id=envelope_msg_id,
        envelope_seq=envelope_seq,
        samples_inserted=inserted,
        samples_deduped=deduped,
        recorded_at=recorded_at,
        verification=verification_metadata if isinstance(verification_metadata, dict) else {},
    )


@telemetry_router.post("/replay/start", response_model=ReplayState)
async def replay_start(
    request: ReplayStartRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ReplayState:
    dev_res = await db.execute(text("SELECT id FROM telemetry.device WHERE slug = :slug LIMIT 1"), {"slug": request.device_slug})
    device_id = dev_res.scalar_one_or_none()
    if not device_id:
        raise HTTPException(status_code=404, detail="device_not_found")

    stream_id = None
    if request.stream_key:
        st = await db.execute(
            text("SELECT id FROM telemetry.stream WHERE device_id = :d AND key = :k LIMIT 1"),
            {"d": str(device_id), "k": request.stream_key},
        )
        stream_id = st.scalar_one_or_none()

    ins = await db.execute(
        text(
            """
            INSERT INTO telemetry.replay_state (
                device_id, stream_id, replay_type, start_time, end_time, current_position,
                playback_speed, is_playing, is_paused, filters, created_by
            ) VALUES (
                :device_id, :stream_id, :replay_type, :start_time, :end_time, :current_position,
                :playback_speed, true, false, :filters::jsonb, :created_by
            )
            RETURNING *
            """
        ),
        {
            "device_id": str(device_id),
            "stream_id": str(stream_id) if stream_id else None,
            "replay_type": request.replay_type,
            "start_time": request.start_time,
            "end_time": request.end_time,
            "current_position": request.start_time,
            "playback_speed": request.playback_speed,
            "filters": json.dumps(request.filters),
            "created_by": request.created_by,
        },
    )
    await db.commit()
    row = ins.mappings().one()
    return ReplayState(**dict(row))


@telemetry_router.get("/replay/{session_id}", response_model=ReplayState)
async def replay_get(session_id: str, db: AsyncSession = Depends(get_db_session)) -> ReplayState:
    res = await db.execute(text("SELECT * FROM telemetry.replay_state WHERE id = :id"), {"id": session_id})
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="replay_not_found")
    return ReplayState(**dict(row))


@telemetry_router.patch("/replay/{session_id}", response_model=ReplayState)
async def replay_update(session_id: str, request: ReplayUpdateRequest, db: AsyncSession = Depends(get_db_session)) -> ReplayState:
    # Update only provided fields.
    stmt = text(
        """
        UPDATE telemetry.replay_state
        SET
            current_position = COALESCE(:current_position, current_position),
            playback_speed = COALESCE(:playback_speed, playback_speed),
            is_playing = COALESCE(:is_playing, is_playing),
            is_paused = COALESCE(:is_paused, is_paused),
            filters = COALESCE(:filters::jsonb, filters),
            updated_at = now()
        WHERE id = :id
        RETURNING *
        """
    )
    res = await db.execute(
        stmt,
        {
            "id": session_id,
            "current_position": request.current_position,
            "playback_speed": request.playback_speed,
            "is_playing": request.is_playing,
            "is_paused": request.is_paused,
            "filters": json.dumps(request.filters) if request.filters is not None else None,
        },
    )
    await db.commit()
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="replay_not_found")
    return ReplayState(**dict(row))


@telemetry_router.delete("/replay/{session_id}")
async def replay_stop(session_id: str, db: AsyncSession = Depends(get_db_session)):
    res = await db.execute(text("DELETE FROM telemetry.replay_state WHERE id = :id"), {"id": session_id})
    await db.commit()
    return {"success": True, "deleted": res.rowcount or 0}


@telemetry_router.post("/health", response_model=DeviceHealthState)
async def health_record(
    request: DeviceHealthStateCreate,
    db: AsyncSession = Depends(get_db_session),
) -> DeviceHealthState:
    dev_res = await db.execute(text("SELECT id FROM telemetry.device WHERE slug = :slug LIMIT 1"), {"slug": request.device_slug})
    device_id = dev_res.scalar_one_or_none()
    if not device_id:
        raise HTTPException(status_code=404, detail="device_not_found")

    ins = await db.execute(
        text(
            """
            INSERT INTO telemetry.device_health_state (
                device_id, recorded_at, status, health_score, metrics, alerts, metadata
            ) VALUES (
                :device_id, COALESCE(:recorded_at, now()), :status, :health_score,
                :metrics::jsonb, :alerts::jsonb, :metadata::jsonb
            )
            RETURNING *
            """
        ),
        {
            "device_id": str(device_id),
            "recorded_at": request.recorded_at,
            "status": request.status,
            "health_score": request.health_score,
            "metrics": json.dumps(request.metrics),
            "alerts": json.dumps(request.alerts),
            "metadata": json.dumps(request.metadata),
        },
    )
    await db.commit()
    row = ins.mappings().one()
    return DeviceHealthState(**dict(row))


@telemetry_router.get("/health/{device_slug}", response_model=list[DeviceHealthState])
async def health_history(device_slug: str, limit: int = 200, db: AsyncSession = Depends(get_db_session)) -> list[DeviceHealthState]:
    dev_res = await db.execute(text("SELECT id FROM telemetry.device WHERE slug = :slug LIMIT 1"), {"slug": device_slug})
    device_id = dev_res.scalar_one_or_none()
    if not device_id:
        raise HTTPException(status_code=404, detail="device_not_found")

    res = await db.execute(
        text(
            """
            SELECT * FROM telemetry.device_health_state
            WHERE device_id = :device_id
            ORDER BY recorded_at DESC
            LIMIT :limit
            """
        ),
        {"device_id": str(device_id), "limit": limit},
    )
    return [DeviceHealthState(**dict(r)) for r in res.mappings().all()]


@telemetry_router.get("/health/summary")
async def health_summary(db: AsyncSession = Depends(get_db_session)):
    res = await db.execute(
        text(
            """
            SELECT status, count(*) AS count
            FROM telemetry.device_health_state
            WHERE recorded_at > now() - interval '24 hours'
            GROUP BY status
            """
        )
    )
    return {"summary": [dict(r) for r in res.mappings().all()]}


@telemetry_router.get("/summary")
async def telemetry_summary(db: AsyncSession = Depends(get_db_session)):
    """Get overall telemetry summary for MYCA world model sensor."""
    # Device count
    device_count = await db.execute(text("SELECT count(*) FROM telemetry.device"))
    total_devices = device_count.scalar_one()

    # Active devices (online status)
    active_count = await db.execute(
        text("SELECT count(*) FROM telemetry.device WHERE status = 'online'")
    )
    active_devices = active_count.scalar_one()

    # Sample count (last 24h)
    sample_count = await db.execute(
        text(
            """
            SELECT count(*) FROM telemetry.sample
            WHERE recorded_at > now() - interval '24 hours'
            """
        )
    )
    recent_samples = sample_count.scalar_one()

    # Stream count
    stream_count = await db.execute(text("SELECT count(*) FROM telemetry.stream"))
    total_streams = stream_count.scalar_one()

    # Latest sample time
    latest = await db.execute(
        text("SELECT max(recorded_at) FROM telemetry.sample")
    )
    latest_sample = latest.scalar_one()

    return {
        "total_devices": total_devices,
        "active_devices": active_devices,
        "total_streams": total_streams,
        "samples_last_24h": recent_samples,
        "latest_sample_at": latest_sample.isoformat() if latest_sample else None,
        "status": "online" if active_devices > 0 else "offline",
    }


@telemetry_router.get("/samples", response_model=list[TelemetrySampleRow])
async def list_samples(
    device_slug: str,
    limit: int = 200,
    db: AsyncSession = Depends(get_db_session),
) -> list[TelemetrySampleRow]:
    dev_res = await db.execute(text("SELECT id FROM telemetry.device WHERE slug = :slug LIMIT 1"), {"slug": device_slug})
    device_id = dev_res.scalar_one_or_none()
    if not device_id:
        raise HTTPException(status_code=404, detail="device_not_found")

    res = await db.execute(
        text(
            """
            SELECT
                st.key AS stream_key,
                sa.recorded_at,
                sa.value_numeric,
                sa.value_json,
                sa.value_unit,
                sa.verified,
                sa.envelope_seq,
                sa.envelope_msg_id
            FROM telemetry.sample sa
            JOIN telemetry.stream st ON st.id = sa.stream_id
            WHERE st.device_id = :device_id
            ORDER BY sa.recorded_at DESC
            LIMIT :limit
            """
        ),
        {"device_id": str(device_id), "limit": limit},
    )
    return [TelemetrySampleRow(**dict(r)) for r in res.mappings().all()]
