from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session, require_api_key
from ..contracts.v1.mycobrain import (
    DeviceCommandCreate,
    DeviceCommandResponse,
    MDPTelemetryIngestionRequest,
    MDPTelemetryIngestionResponse,
    MycoBrainDeviceCreate,
    MycoBrainDeviceResponse,
    MycoBrainStatusResponse,
)

mycobrain_router = APIRouter(
    prefix="/mycobrain",
    tags=["mycobrain"],
    dependencies=[Depends(require_api_key)],
)


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


@mycobrain_router.post("/devices", response_model=MycoBrainDeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_device(
    device: MycoBrainDeviceCreate,
    db: AsyncSession = Depends(get_db_session),
) -> MycoBrainDeviceResponse:
    # Enforce unique serial
    existing = await db.execute(
        text("SELECT id FROM telemetry.device WHERE serial_number = :sn"),
        {"sn": device.serial_number},
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="serial_number already exists")

    api_key_hash = _hash_api_key(device.api_key) if device.api_key else None

    row = (await db.execute(
        text(
            """
            INSERT INTO telemetry.device (name, slug, device_type, serial_number, firmware_version, api_key_hash, power_status, status)
            VALUES (:name, :slug, 'mycobrain_v1', :sn, :fw, :akh, :ps, 'active')
            RETURNING id, name, slug, status, serial_number, firmware_version, power_status, created_at, updated_at
            """
        ),
        {
            "name": device.name,
            "slug": device.slug,
            "sn": device.serial_number,
            "fw": device.firmware_version,
            "akh": api_key_hash,
            "ps": device.power_status,
        },
    )).mappings().one()

    await db.execute(
        text(
            """
            INSERT INTO telemetry.mycobrain_device (id, side_a_firmware_version, side_b_firmware_version, i2c_addresses, analog_channels, mosfet_states, telemetry_interval_seconds)
            VALUES (:id, :sa, :sb, :i2c, :analog, :mosfet, :interval)
            """
        ),
        {
            "id": row["id"],
            "sa": device.side_a_firmware_version,
            "sb": device.side_b_firmware_version,
            "i2c": json.dumps(device.i2c_addresses),
            "analog": json.dumps(device.analog_channels),
            "mosfet": json.dumps(device.mosfet_states),
            "interval": device.telemetry_interval_seconds,
        },
    )

    await db.commit()

    return MycoBrainDeviceResponse(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        status=row["status"],
        serial_number=row["serial_number"],
        firmware_version=row["firmware_version"],
        power_status=row["power_status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@mycobrain_router.post("/telemetry/ingest", response_model=MDPTelemetryIngestionResponse)
async def ingest_telemetry(
    request: MDPTelemetryIngestionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MDPTelemetryIngestionResponse:
    t = request.telemetry

    # Load device
    res = await db.execute(
        text(
            """
            SELECT id, api_key_hash
            FROM telemetry.device
            WHERE device_type = 'mycobrain_v1'
              AND serial_number = :sn
            """
        ),
        {"sn": t.device_serial_number},
    )
    device = res.mappings().one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="device not found")

    if device["api_key_hash"] and request.api_key:
        if _hash_api_key(request.api_key) != device["api_key_hash"]:
            raise HTTPException(status_code=401, detail="invalid device api key")

    # Idempotency
    dup = await db.execute(
        text(
            """
            SELECT 1
            FROM telemetry.mdp_telemetry_log
            WHERE device_id = :did AND mdp_sequence_number = :seq AND mdp_timestamp = :ts
            """
        ),
        {"did": device["id"], "seq": t.mdp_sequence_number, "ts": t.mdp_timestamp},
    )
    if dup.scalar_one_or_none():
        return MDPTelemetryIngestionResponse(
            success=True,
            device_id=device["id"],
            samples_created=0,
            duplicate=True,
            message="duplicate telemetry ignored",
        )

    await db.execute(
        text(
            """
            INSERT INTO telemetry.mdp_telemetry_log (device_id, mdp_sequence_number, mdp_timestamp, mdp_message_type, raw_payload)
            VALUES (:did, :seq, :ts, 'telemetry', :raw)
            """
        ),
        {"did": device["id"], "seq": t.mdp_sequence_number, "ts": t.mdp_timestamp, "raw": json.dumps(t.model_dump())},
    )

    # Helper to ensure stream exists
    async def ensure_stream(key: str, unit: Optional[str]) -> None:
        await db.execute(
            text(
                """
                INSERT INTO telemetry.stream (device_id, key, unit)
                VALUES (:did, :key, :unit)
                ON CONFLICT (device_id, key) DO NOTHING
                """
            ),
            {"did": device["id"], "key": key, "unit": unit},
        )

    async def insert_sample(key: str, value: float, unit: Optional[str]) -> None:
        await db.execute(
            text(
                """
                INSERT INTO telemetry.sample (stream_id, recorded_at, value_numeric, value_unit, metadata)
                SELECT id, :ts, :val, :unit, :meta
                FROM telemetry.stream
                WHERE device_id = :did AND key = :key
                """
            ),
            {"did": device["id"], "key": key, "ts": t.mdp_timestamp, "val": value, "unit": unit, "meta": json.dumps(t.metadata)},
        )

    samples = 0

    mapping = [
        ("bme688.temperature", t.bme688_temperature, "C"),
        ("bme688.humidity", t.bme688_humidity, "%"),
        ("bme688.pressure", t.bme688_pressure, "hPa"),
        ("bme688.gas_resistance", t.bme688_gas_resistance, "ohm"),
        ("ai1.voltage", t.ai1_voltage, "V"),
        ("ai2.voltage", t.ai2_voltage, "V"),
        ("ai3.voltage", t.ai3_voltage, "V"),
        ("ai4.voltage", t.ai4_voltage, "V"),
    ]

    for key, val, unit in mapping:
        if val is None:
            continue
        await ensure_stream(key, unit)
        await insert_sample(key, float(val), unit)
        samples += 1

    for mosfet_key, state in t.mosfet_states.items():
        key = "mosfet." + str(mosfet_key)
        await ensure_stream(key, "bool")
        await insert_sample(key, 1.0 if state else 0.0, "bool")
        samples += 1

    await db.execute(
        text("UPDATE telemetry.device SET last_seen_at = :now, mdp_sequence_number = :seq WHERE id = :did"),
        {"did": device["id"], "now": datetime.utcnow(), "seq": t.mdp_sequence_number},
    )

    await db.execute(
        text(
            """
            UPDATE telemetry.mycobrain_device
            SET mdp_last_telemetry_at = :ts,
                mdp_last_sequence = :seq,
                mosfet_states = :mosfet,
                i2c_addresses = :i2c,
                updated_at = now()
            WHERE id = :did
            """
        ),
        {"did": device["id"], "ts": t.mdp_timestamp, "seq": t.mdp_sequence_number, "mosfet": json.dumps(t.mosfet_states), "i2c": json.dumps(t.i2c_addresses or [])},
    )

    await db.commit()

    return MDPTelemetryIngestionResponse(
        success=True,
        device_id=device["id"],
        samples_created=samples,
        duplicate=False,
        message="ingested telemetry",
    )


@mycobrain_router.get("/devices/{device_id}/status", response_model=MycoBrainStatusResponse)
async def get_status(device_id: UUID, db: AsyncSession = Depends(get_db_session)) -> MycoBrainStatusResponse:
    row = (await db.execute(
        text("SELECT * FROM app.v_mycobrain_status WHERE device_id = :id"),
        {"id": device_id},
    )).mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="device not found")
    return MycoBrainStatusResponse(**dict(row))


@mycobrain_router.post("/devices/{device_id}/commands", response_model=DeviceCommandResponse, status_code=201)
async def create_command(device_id: UUID, cmd: DeviceCommandCreate, db: AsyncSession = Depends(get_db_session)) -> DeviceCommandResponse:
    import uuid

    command_id = str(uuid.uuid4())
    row = (await db.execute(
        text(
            """
            INSERT INTO telemetry.device_command (device_id, command_type, command_id, payload, priority, expires_at)
            VALUES (:did, :ctype, :cid, :payload, :prio, :exp)
            RETURNING id, device_id, command_type, command_id, status, payload, priority, created_at, expires_at
            """
        ),
        {
            "did": device_id,
            "ctype": cmd.command_type,
            "cid": command_id,
            "payload": json.dumps(cmd.payload),
            "prio": cmd.priority,
            "exp": cmd.expires_at,
        },
    )).mappings().one()

    await db.commit()

    return DeviceCommandResponse(
        id=row["id"],
        device_id=row["device_id"],
        command_type=row["command_type"],
        command_id=row["command_id"],
        status=row["status"],
        payload=row["payload"],
        priority=row["priority"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )
