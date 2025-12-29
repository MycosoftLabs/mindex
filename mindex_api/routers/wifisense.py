"""WiFi Sense API router for MINDEX."""

from datetime import datetime, timedelta
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from ..dependencies import get_db, pagination_params
from ..schemas.wifisense import (
    WiFiSenseDeviceCreate,
    WiFiSenseDeviceResponse,
    WiFiSenseCSIIngest,
    WiFiSensePresenceEvent,
    WiFiSenseTrack,
    WiFiSenseStatusResponse,
)

router = APIRouter(prefix="/wifisense", tags=["wifisense"])


@router.post("/devices", response_model=WiFiSenseDeviceResponse)
async def create_wifisense_device(
    device: WiFiSenseDeviceCreate,
    db=Depends(get_db),
):
    """Create WiFi Sense device configuration."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO telemetry.wifisense_device 
                (device_id, link_id, channel, bandwidth, csi_format, 
                 num_antennas, num_subcarriers, calibration_data)
                VALUES (:device_id, :link_id, :channel, :bandwidth, :csi_format,
                        :num_antennas, :num_subcarriers, :calibration_data::jsonb)
                RETURNING *
            """),
            {
                "device_id": str(device.device_id),
                "link_id": device.link_id,
                "channel": device.channel,
                "bandwidth": device.bandwidth,
                "csi_format": device.csi_format,
                "num_antennas": device.num_antennas,
                "num_subcarriers": device.num_subcarriers,
                "calibration_data": device.calibration_data,
            },
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(500, "Failed to create device")
        return dict(row._mapping)


@router.get("/devices/{device_id}", response_model=WiFiSenseDeviceResponse)
async def get_wifisense_device(device_id: UUID, db=Depends(get_db)):
    """Get WiFi Sense device configuration."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT * FROM telemetry.wifisense_device
                WHERE device_id = :device_id
            """),
            {"device_id": str(device_id)},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(404, "Device not found")
        return dict(row._mapping)


@router.post("/ingest/csi")
async def ingest_csi(data: WiFiSenseCSIIngest, db=Depends(get_db)):
    """Ingest WiFi Sense CSI data."""
    async with db.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO telemetry.wifisense_csi
                (device_id, link_id, timestamp_ns, channel, rssi, csi_data,
                 csi_length, csi_format, num_subcarriers, num_antennas)
                VALUES (:device_id, :link_id, :timestamp_ns, :channel, :rssi,
                        :csi_data, :csi_length, :csi_format, :num_subcarriers, :num_antennas)
            """),
            {
                "device_id": str(data.device_id),
                "link_id": data.link_id,
                "timestamp_ns": data.timestamp_ns,
                "channel": data.channel,
                "rssi": data.rssi,
                "csi_data": data.csi_data,
                "csi_length": data.csi_length,
                "csi_format": data.csi_format,
                "num_subcarriers": data.num_subcarriers,
                "num_antennas": data.num_antennas,
            },
        )
    return {"status": "ingested", "device_id": str(data.device_id)}


@router.post("/events/presence")
async def create_presence_event(
    event: WiFiSensePresenceEvent,
    db=Depends(get_db),
):
    """Create WiFi Sense presence event."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO telemetry.wifisense_presence
                (zone_id, timestamp, presence_type, confidence, metadata)
                VALUES (:zone_id, :timestamp, :presence_type, :confidence, :metadata::jsonb)
                RETURNING id
            """),
            {
                "zone_id": event.zone_id,
                "timestamp": event.timestamp,
                "presence_type": event.presence_type,
                "confidence": event.confidence,
                "metadata": event.metadata,
            },
        )
        row = result.fetchone()
        return {"id": str(row.id), "status": "created"}


@router.get("/events", response_model=List[WiFiSensePresenceEvent])
async def get_presence_events(
    zone_id: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    pagination=Depends(pagination_params),
    db=Depends(get_db),
):
    """Get WiFi Sense presence events."""
    async with db.begin() as conn:
        query = "SELECT * FROM telemetry.wifisense_presence WHERE 1=1"
        params = {}
        
        if zone_id:
            query += " AND zone_id = :zone_id"
            params["zone_id"] = zone_id
        
        if since:
            query += " AND timestamp >= :since"
            params["since"] = since
        
        query += " ORDER BY timestamp DESC LIMIT :limit OFFSET :offset"
        params["limit"] = pagination["limit"]
        params["offset"] = pagination["offset"]
        
        result = await conn.execute(text(query), params)
        return [dict(row._mapping) for row in result]


@router.get("/tracks", response_model=List[WiFiSenseTrack])
async def get_tracks(
    zone_id: Optional[str] = Query(None),
    active: bool = Query(True),
    db=Depends(get_db),
):
    """Get WiFi Sense tracks."""
    async with db.begin() as conn:
        query = "SELECT * FROM telemetry.wifisense_track WHERE 1=1"
        params = {}
        
        if zone_id:
            query += " AND zone_id = :zone_id"
            params["zone_id"] = zone_id
        
        if active:
            # Tracks seen in last 5 minutes
            query += " AND last_seen >= :active_since"
            params["active_since"] = datetime.utcnow() - timedelta(minutes=5)
        
        query += " ORDER BY last_seen DESC"
        
        result = await conn.execute(text(query), params)
        return [dict(row._mapping) for row in result]


@router.get("/status", response_model=List[WiFiSenseStatusResponse])
async def get_wifisense_status(db=Depends(get_db)):
    """Get WiFi Sense status for all devices."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("SELECT * FROM app.v_wifisense_status")
        )
        return [dict(row._mapping) for row in result]

