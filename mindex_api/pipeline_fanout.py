from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings

logger = logging.getLogger(__name__)


def _extract_position(payload: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    for key in ("position", "location", "geo", "coordinates"):
        value = payload.get(key)
        if isinstance(value, dict):
            lat = value.get("lat") or value.get("latitude")
            lon = value.get("lon") or value.get("lng") or value.get("longitude")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                return float(lat), float(lon)
    return None, None


def _extract_confidence(payload: Dict[str, Any]) -> float:
    value = payload.get("confidence")
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return 0.5


async def fanout_to_natureos_envelope(envelope: Dict[str, Any], source: str) -> None:
    """
    Forward normalized telemetry envelopes from MINDEX to NatureOS when configured.

    This is best-effort and must not break ingestion if NatureOS is unavailable.
    """
    endpoint = (settings.natureos_api_endpoint or "").rstrip("/")
    if not endpoint:
        return

    url = f"{endpoint}{settings.natureos_ingest_path}"
    headers: Dict[str, str] = {}
    if settings.natureos_webhook_secret:
        headers["X-Webhook-Secret"] = settings.natureos_webhook_secret
    if settings.natureos_api_key:
        headers["X-API-Key"] = settings.natureos_api_key
    headers["X-Source-System"] = source

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=envelope, headers=headers or None)
            if response.status_code >= 300:
                logger.warning(
                    "NatureOS fanout failed status=%s body=%s",
                    response.status_code,
                    response.text[:300],
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("NatureOS fanout exception: %s", exc)


async def mirror_to_fusarium(
    db: AsyncSession,
    *,
    source_id: str,
    payload: Dict[str, Any],
    recorded_at: Optional[datetime] = None,
) -> None:
    """
    Mirror telemetry into Fusarium analytics tables in MINDEX.

    Creates/updates a lightweight entity track + correlation event so Fusarium
    can consume MycoBrain data without waiting on an external push layer.
    """
    if not settings.fusarium_fanout_enabled:
        return

    label = str(payload.get("label") or payload.get("classification") or "mycobrain_telemetry")
    confidence = _extract_confidence(payload)
    lat, lon = _extract_position(payload)
    event_time = recorded_at or datetime.now(timezone.utc)

    track_stmt = text(
        """
        INSERT INTO fusarium.entity_tracks (
            track_id, latest_label, confidence, first_seen, last_seen, last_position
        ) VALUES (
            gen_random_uuid(),
            :latest_label,
            :confidence,
            :event_time,
            :event_time,
            CASE
                WHEN :longitude IS NOT NULL AND :latitude IS NOT NULL
                THEN ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
                ELSE NULL
            END
        )
        RETURNING track_id
        """
    )
    track_res = await db.execute(
        track_stmt,
        {
            "latest_label": label,
            "confidence": confidence,
            "event_time": event_time,
            "latitude": lat,
            "longitude": lon,
        },
    )
    track_id = track_res.scalar_one()

    event_stmt = text(
        """
        INSERT INTO fusarium.correlation_events (
            event_id, entity_id, domains, confidence, payload, created_at
        ) VALUES (
            gen_random_uuid(),
            :entity_id::uuid,
            :domains::text[],
            :confidence,
            :payload::jsonb,
            :created_at
        )
        """
    )
    await db.execute(
        event_stmt,
        {
            "entity_id": str(track_id),
            "domains": ["mycobrain", "telemetry", "fci"],
            "confidence": confidence,
            "payload": json.dumps(
                {
                    "source_id": source_id,
                    "recorded_at": event_time.isoformat(),
                    "telemetry": payload,
                }
            ),
            "created_at": event_time,
        },
    )
