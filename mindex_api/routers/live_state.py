from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

router = APIRouter(prefix="/state", tags=["state", "integration"])


@router.get("/live")
async def get_live_integration_state(
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """
    Consolidated live state for MYCA and downstream systems.

    Includes:
    - Latest telemetry sample
    - Latest NLM packet
    - Latest experience packet with self/world/fingerprint/merkle provenance
    - Latest Merkle roots observed in MICA tables
    """
    response: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "telemetry": {},
        "nlm": {},
        "experience_packet": {},
        "merkle": {},
    }

    try:
        telemetry_stmt = text(
            """
            SELECT
                d.slug AS device_slug,
                s.recorded_at,
                s.verified,
                s.value_num,
                c.name AS channel_name,
                st.name AS stream_name
            FROM telemetry.sample s
            JOIN telemetry.channel c ON c.id = s.channel_id
            JOIN telemetry.stream st ON st.id = c.stream_id
            JOIN telemetry.device d ON d.id = st.device_id
            ORDER BY s.recorded_at DESC
            LIMIT 1
            """
        )
        row = (await db.execute(telemetry_stmt)).mappings().first()
        if row:
            response["telemetry"] = dict(row)
    except Exception as exc:  # noqa: BLE001
        response["telemetry_error"] = str(exc)

    try:
        nlm_stmt = text(
            """
            SELECT source_id, anomaly_score, packet, ts
            FROM nlm.nature_embeddings
            ORDER BY ts DESC
            LIMIT 1
            """
        )
        row = (await db.execute(nlm_stmt)).mappings().first()
        if row:
            response["nlm"] = {
                "source_id": row["source_id"],
                "anomaly_score": row["anomaly_score"],
                "packet": row["packet"],
                "ts": row["ts"].isoformat() if row.get("ts") else None,
            }
    except Exception as exc:  # noqa: BLE001
        response["nlm_error"] = str(exc)

    try:
        ep_stmt = text(
            """
            SELECT id, session_id, user_id, self_state, world_state, provenance, created_at
            FROM experience_packets
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        row = (await db.execute(ep_stmt)).mappings().first()
        if row:
            provenance = row["provenance"] or {}
            response["experience_packet"] = {
                "id": row["id"],
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "self_state": row["self_state"] or {},
                "world_state": row["world_state"] or {},
                "fingerprint_state": provenance.get("fingerprint_state", {}),
                "merkle_roots": provenance.get("merkle_roots", {}),
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            }
    except Exception as exc:  # noqa: BLE001
        response["experience_packet_error"] = str(exc)

    try:
        merkle_stmt = text(
            """
            SELECT root_type, encode(root_hash, 'hex') AS root_hash_hex, tick_id, created_at
            FROM mica.root_record
            ORDER BY created_at DESC
            LIMIT 8
            """
        )
        rows = (await db.execute(merkle_stmt)).mappings().all()
        if rows:
            response["merkle"] = {"recent_roots": [dict(row) for row in rows]}
    except Exception as exc:  # noqa: BLE001
        response["merkle_error"] = str(exc)

    return response
