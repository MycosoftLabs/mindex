"""
Meshtastic mesh data — internal ingest + list APIs (MAS bridge, MAS UI).

Mounted under ``/api/mindex/internal`` with ``X-Internal-Token`` (see main.py).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

logger = logging.getLogger(__name__)

meshtastic_router = APIRouter(prefix="/meshtastic", tags=["meshtastic"])


def _node_hex(n: Optional[int]) -> Optional[str]:
    if n is None:
        return None
    try:
        return f"!{int(n) & 0xFFFFFFFF:08x}"
    except (TypeError, ValueError):
        return None


class NodeUpsertBody(BaseModel):
    node_id: str = Field(..., description="Meshtastic node id e.g. !deadbeef")
    long_name: Optional[str] = None
    short_name: Optional[str] = None
    hw_model: Optional[str] = None
    role: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    last_heard_at: Optional[datetime] = None
    battery_pct: Optional[float] = None
    voltage: Optional[float] = None
    channel_util: Optional[float] = None
    air_util_tx: Optional[float] = None
    firmware: Optional[str] = None
    region: Optional[str] = None
    modem_preset: Optional[str] = None
    is_licensed: Optional[bool] = None
    is_observer: Optional[bool] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PacketIngestBody(BaseModel):
    packet_uid: str
    from_node_id: Optional[str] = None
    to_node_id: Optional[str] = None
    gateway_node_id: Optional[str] = None
    channel: Optional[str] = None
    port_num: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    payload_text: Optional[str] = None
    rx_time: Optional[datetime] = None
    rx_rssi: Optional[float] = None
    rx_snr: Optional[float] = None
    hop_limit: Optional[int] = None
    hop_start: Optional[int] = None
    want_ack: Optional[bool] = None
    via_mqtt: bool = True
    topic: Optional[str] = None
    raw_b64: Optional[str] = None


class ObserverUpsertBody(BaseModel):
    observer_id: str
    node_id: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    region: Optional[str] = None
    gateway_kind: str = Field(..., pattern="^(mqtt|lora|mycobrain)$")
    online: bool = True
    pkts_per_min: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RouteTouchBody(BaseModel):
    from_node_id: str
    to_node_id: str
    hops: Optional[int] = None
    snr: Optional[float] = None


@meshtastic_router.post("/ingest/node", summary="Upsert mesh node")
async def ingest_node(
    body: NodeUpsertBody,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    pos_sql = "NULL::geography"
    params: Dict[str, Any] = {
        "node_id": body.node_id.strip(),
        "long_name": body.long_name,
        "short_name": body.short_name,
        "hw_model": body.hw_model,
        "role": body.role,
        "last_heard_at": body.last_heard_at or datetime.now(timezone.utc),
        "battery_pct": body.battery_pct,
        "voltage": body.voltage,
        "channel_util": body.channel_util,
        "air_util_tx": body.air_util_tx,
        "firmware": body.firmware,
        "region": body.region,
        "modem_preset": body.modem_preset,
        "is_licensed": body.is_licensed if body.is_licensed is not None else False,
        "is_observer": body.is_observer if body.is_observer is not None else False,
        "metadata": json.dumps(body.metadata),
    }
    if body.lat is not None and body.lon is not None:
        pos_sql = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography"
        params["lat"] = body.lat
        params["lon"] = body.lon
    stmt = text(
        f"""
        INSERT INTO meshtastic.nodes (
            node_id, long_name, short_name, hw_model, role, position, last_heard_at,
            battery_pct, voltage, channel_util, air_util_tx, firmware, region, modem_preset,
            is_licensed, is_observer, metadata, updated_at
        ) VALUES (
            :node_id, :long_name, :short_name, :hw_model, :role, {pos_sql}, :last_heard_at,
            :battery_pct, :voltage, :channel_util, :air_util_tx, :firmware, :region, :modem_preset,
            :is_licensed, :is_observer, CAST(:metadata AS jsonb), now()
        )
        ON CONFLICT (node_id) DO UPDATE SET
            long_name = COALESCE(EXCLUDED.long_name, meshtastic.nodes.long_name),
            short_name = COALESCE(EXCLUDED.short_name, meshtastic.nodes.short_name),
            hw_model = COALESCE(EXCLUDED.hw_model, meshtastic.nodes.hw_model),
            role = COALESCE(EXCLUDED.role, meshtastic.nodes.role),
            position = COALESCE(EXCLUDED.position, meshtastic.nodes.position),
            last_heard_at = GREATEST(COALESCE(meshtastic.nodes.last_heard_at, EXCLUDED.last_heard_at),
                                     EXCLUDED.last_heard_at),
            battery_pct = COALESCE(EXCLUDED.battery_pct, meshtastic.nodes.battery_pct),
            voltage = COALESCE(EXCLUDED.voltage, meshtastic.nodes.voltage),
            channel_util = COALESCE(EXCLUDED.channel_util, meshtastic.nodes.channel_util),
            air_util_tx = COALESCE(EXCLUDED.air_util_tx, meshtastic.nodes.air_util_tx),
            firmware = COALESCE(EXCLUDED.firmware, meshtastic.nodes.firmware),
            region = COALESCE(EXCLUDED.region, meshtastic.nodes.region),
            modem_preset = COALESCE(EXCLUDED.modem_preset, meshtastic.nodes.modem_preset),
            is_licensed = COALESCE(EXCLUDED.is_licensed, meshtastic.nodes.is_licensed),
            is_observer = COALESCE(EXCLUDED.is_observer, meshtastic.nodes.is_observer),
            metadata = meshtastic.nodes.metadata || EXCLUDED.metadata,
            updated_at = now()
        RETURNING id::text
        """
    )
    res = await db.execute(stmt, params)
    await db.commit()
    row = res.mappings().first()
    return {"status": "ok", "id": row["id"] if row else None}


@meshtastic_router.post("/ingest/packet", summary="Insert mesh packet (idempotent on packet_uid)")
async def ingest_packet(
    body: PacketIngestBody,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    rx = body.rx_time or datetime.now(timezone.utc)
    stmt = text(
        """
        INSERT INTO meshtastic.packets (
            packet_uid, from_node_id, to_node_id, gateway_node_id, channel, port_num,
            payload, payload_text, rx_time, rx_rssi, rx_snr, hop_limit, hop_start,
            want_ack, via_mqtt, topic, raw_b64
        ) VALUES (
            :packet_uid, :from_node_id, :to_node_id, :gateway_node_id, :channel, :port_num,
            CAST(:payload AS jsonb), :payload_text, :rx_time, :rx_rssi, :rx_snr, :hop_limit, :hop_start,
            :want_ack, :via_mqtt, :topic, :raw_b64
        )
        ON CONFLICT (packet_uid) DO NOTHING
        RETURNING id
        """
    )
    res = await db.execute(
        stmt,
        {
            "packet_uid": body.packet_uid,
            "from_node_id": body.from_node_id,
            "to_node_id": body.to_node_id,
            "gateway_node_id": body.gateway_node_id,
            "channel": body.channel,
            "port_num": body.port_num,
            "payload": json.dumps(body.payload),
            "payload_text": body.payload_text,
            "rx_time": rx,
            "rx_rssi": body.rx_rssi,
            "rx_snr": body.rx_snr,
            "hop_limit": body.hop_limit,
            "hop_start": body.hop_start,
            "want_ack": body.want_ack if body.want_ack is not None else False,
            "via_mqtt": body.via_mqtt,
            "topic": body.topic,
            "raw_b64": body.raw_b64,
        },
    )
    await db.commit()
    row = res.mappings().first()
    return {"status": "ok", "inserted": row is not None, "id": row["id"] if row else None}


@meshtastic_router.post("/ingest/observer", summary="Upsert mesh observer / gateway")
async def ingest_observer(
    body: ObserverUpsertBody,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    pos_sql = "NULL::geography"
    params: Dict[str, Any] = {
        "observer_id": body.observer_id,
        "node_id": body.node_id,
        "region": body.region,
        "gateway_kind": body.gateway_kind,
        "online": body.online,
        "pkts_per_min": body.pkts_per_min,
        "metadata": json.dumps(body.metadata),
    }
    if body.lat is not None and body.lon is not None:
        pos_sql = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography"
        params["lat"] = body.lat
        params["lon"] = body.lon
    stmt = text(
        f"""
        INSERT INTO meshtastic.observers (
            observer_id, node_id, position, region, gateway_kind, online, pkts_per_min, last_seen_at, metadata
        ) VALUES (
            :observer_id, :node_id, {pos_sql}, :region, :gateway_kind, :online, :pkts_per_min, now(), CAST(:metadata AS jsonb)
        )
        ON CONFLICT (observer_id, gateway_kind) DO UPDATE SET
            node_id = COALESCE(EXCLUDED.node_id, meshtastic.observers.node_id),
            position = COALESCE(EXCLUDED.position, meshtastic.observers.position),
            region = COALESCE(EXCLUDED.region, meshtastic.observers.region),
            online = EXCLUDED.online,
            pkts_per_min = COALESCE(EXCLUDED.pkts_per_min, meshtastic.observers.pkts_per_min),
            last_seen_at = now(),
            metadata = meshtastic.observers.metadata || EXCLUDED.metadata
        RETURNING id::text
        """
    )
    res = await db.execute(stmt, params)
    await db.commit()
    row = res.mappings().first()
    return {"status": "ok", "id": row["id"] if row else None}


@meshtastic_router.post("/ingest/route", summary="Touch aggregated route stats")
async def ingest_route(
    body: RouteTouchBody,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    stmt = text(
        """
        INSERT INTO meshtastic.routes (from_node_id, to_node_id, hops, last_seen_at, packet_count, avg_snr)
        VALUES (:from_id, :to_id, :hops, now(), 1, :snr)
        ON CONFLICT (from_node_id, to_node_id) DO UPDATE SET
            hops = COALESCE(EXCLUDED.hops, meshtastic.routes.hops),
            last_seen_at = now(),
            packet_count = meshtastic.routes.packet_count + 1,
            avg_snr = CASE
                WHEN EXCLUDED.avg_snr IS NULL THEN meshtastic.routes.avg_snr
                WHEN meshtastic.routes.avg_snr IS NULL THEN EXCLUDED.avg_snr
                ELSE (meshtastic.routes.avg_snr * 0.9 + EXCLUDED.avg_snr * 0.1)
            END
        """
    )
    await db.execute(
        stmt,
        {"from_id": body.from_node_id, "to_id": body.to_node_id, "hops": body.hops, "snr": body.snr},
    )
    await db.commit()
    return {"status": "ok"}


@meshtastic_router.get("/nodes", summary="List mesh nodes")
async def list_nodes(
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    stmt = text(
        """
        SELECT
            node_id, long_name, short_name, hw_model, role,
            ST_Y(position::geometry) AS lat, ST_X(position::geometry) AS lon,
            last_heard_at, battery_pct, voltage, channel_util, air_util_tx,
            firmware, region, modem_preset, is_licensed, is_observer, metadata, updated_at
        FROM meshtastic.nodes
        ORDER BY last_heard_at DESC NULLS LAST
        LIMIT :limit OFFSET :offset
        """
    )
    res = await db.execute(stmt, {"limit": limit, "offset": offset})
    rows = [dict(r) for r in res.mappings().all()]
    return {"items": rows, "limit": limit, "offset": offset}


@meshtastic_router.get("/packets", summary="Recent mesh packets")
async def list_packets(
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    since: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    if since:
        stmt = text(
            """
            SELECT id, packet_uid, from_node_id, to_node_id, gateway_node_id, channel, port_num,
                   payload, payload_text, rx_time, rx_rssi, rx_snr, hop_limit, hop_start,
                   want_ack, via_mqtt, topic
            FROM meshtastic.packets
            WHERE rx_time >= :since
            ORDER BY rx_time DESC
            LIMIT :limit OFFSET :offset
            """
        )
        res = await db.execute(stmt, {"since": since, "limit": limit, "offset": offset})
    else:
        stmt = text(
            """
            SELECT id, packet_uid, from_node_id, to_node_id, gateway_node_id, channel, port_num,
                   payload, payload_text, rx_time, rx_rssi, rx_snr, hop_limit, hop_start,
                   want_ack, via_mqtt, topic
            FROM meshtastic.packets
            ORDER BY rx_time DESC
            LIMIT :limit OFFSET :offset
            """
        )
        res = await db.execute(stmt, {"limit": limit, "offset": offset})
    rows = [dict(r) for r in res.mappings().all()]
    return {"items": rows, "limit": limit, "offset": offset}


@meshtastic_router.get("/observers", summary="List mesh observers")
async def list_observers(
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    stmt = text(
        """
        SELECT observer_id, node_id, ST_Y(position::geometry) AS lat, ST_X(position::geometry) AS lon,
               region, gateway_kind, online, pkts_per_min, last_seen_at, metadata
        FROM meshtastic.observers
        ORDER BY last_seen_at DESC
        """
    )
    res = await db.execute(stmt)
    return {"items": [dict(r) for r in res.mappings().all()]}


@meshtastic_router.get("/routes", summary="List mesh routes")
async def list_routes(
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    stmt = text(
        """
        SELECT from_node_id, to_node_id, hops, last_seen_at, packet_count, avg_snr
        FROM meshtastic.routes
        ORDER BY last_seen_at DESC
        LIMIT :limit
        """
    )
    res = await db.execute(stmt, {"limit": limit})
    return {"items": [dict(r) for r in res.mappings().all()]}


@meshtastic_router.get("/stats", summary="Aggregate stats for dashboards")
async def mesh_stats(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    n = await db.execute(text("SELECT count(*)::int AS c FROM meshtastic.nodes"))
    p1 = await db.execute(text("SELECT count(*)::int AS c FROM meshtastic.packets WHERE rx_time > now() - interval '1 minute'"))
    p60 = await db.execute(text("SELECT count(*)::int AS c FROM meshtastic.packets WHERE rx_time > now() - interval '60 minutes'"))
    o = await db.execute(text("SELECT count(*)::int AS c FROM meshtastic.observers WHERE online = true"))
    row_n = n.mappings().first()
    row_p1 = p1.mappings().first()
    row_p60 = p60.mappings().first()
    row_o = o.mappings().first()
    return {
        "node_count": row_n["c"] if row_n else 0,
        "packets_last_1m": row_p1["c"] if row_p1 else 0,
        "packets_last_60m": row_p60["c"] if row_p60 else 0,
        "observers_online": row_o["c"] if row_o else 0,
    }
