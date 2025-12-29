"""
MycoBrain Device API Router

Provides endpoints for:
- Device registration and management
- Telemetry ingestion (MDP v1 and NDJSON)
- Command queuing and acknowledgement
- Automation rules
- NatureOS widget configuration
- Mycorrhizae Protocol integration
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import (
    PaginationParams,
    get_db_session,
    pagination_params,
    require_api_key,
)
from ..protocols.mycorrhizae import MycorrhizaeMessage, get_protocol
from ..schemas.mycobrain import (
    AutomationRuleCreate,
    AutomationRuleResponse,
    CommandAckRequest,
    CommandCreateRequest,
    CommandListResponse,
    CommandResponse,
    CommandStatus,
    ConnectivityStatus,
    DeviceAPIKeyResponse,
    FirmwareUpdateRequest,
    I2CScanRequest,
    LatestReadingsResponse,
    MOSFETControlRequest,
    MycoBrainDeviceCreate,
    MycoBrainDeviceListResponse,
    MycoBrainDeviceResponse,
    MycoBrainDeviceUpdate,
    MycorrhizaeMessageResponse,
    MycorrhizaePublishRequest,
    NatureOSWidgetConfig,
    RebootRequest,
    TelemetryBatchIngestRequest,
    TelemetryBatchIngestResponse,
    TelemetryIngestRequest,
    TelemetryIngestResponse,
    TelemetryIntervalRequest,
)

# ============================================================================
# ROUTERS
# ============================================================================

mycobrain_router = APIRouter(
    prefix="/mycobrain",
    tags=["mycobrain"],
    dependencies=[Depends(require_api_key)],
)

# Sub-routers
devices_router = APIRouter(prefix="/devices", tags=["mycobrain-devices"])
telemetry_router = APIRouter(prefix="/telemetry", tags=["mycobrain-telemetry"])
commands_router = APIRouter(prefix="/commands", tags=["mycobrain-commands"])
automation_router = APIRouter(prefix="/automation", tags=["mycobrain-automation"])
mycorrhizae_router = APIRouter(prefix="/mycorrhizae", tags=["mycorrhizae-protocol"])


# ============================================================================
# DEVICE MANAGEMENT
# ============================================================================

@devices_router.get("", response_model=MycoBrainDeviceListResponse)
async def list_mycobrain_devices(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    device_type: Optional[str] = None,
    connectivity_status: Optional[str] = None,
) -> MycoBrainDeviceListResponse:
    """List all registered MycoBrain devices."""
    stmt = text("""
        SELECT 
            d.*,
            td.name as telemetry_device_name,
            ST_AsGeoJSON(td.location::geometry) as location_geojson,
            CASE 
                WHEN d.last_seen_at > now() - interval '2 minutes' THEN 'online'
                WHEN d.last_seen_at > now() - interval '10 minutes' THEN 'stale'
                ELSE 'offline'
            END as connectivity_status,
            (SELECT count(*) FROM mycobrain.command_queue cq 
             WHERE cq.device_id = d.id AND cq.status = 'pending') as pending_commands
        FROM mycobrain.device d
        LEFT JOIN telemetry.device td ON td.id = d.telemetry_device_id
        WHERE (:device_type IS NULL OR d.device_type::text = :device_type)
        ORDER BY d.created_at DESC
        LIMIT :limit OFFSET :offset
    """)
    
    count_stmt = text("""
        SELECT count(*) FROM mycobrain.device d
        WHERE (:device_type IS NULL OR d.device_type::text = :device_type)
    """)
    
    params = {
        "device_type": device_type,
        "limit": pagination.limit,
        "offset": pagination.offset,
    }
    
    result = await db.execute(stmt, params)
    count_result = await db.execute(count_stmt, params)
    total = count_result.scalar_one()
    
    devices = []
    for row in result.mappings().all():
        data = dict(row)
        loc = data.pop("location_geojson", None)
        data["location"] = json.loads(loc) if loc else None
        data["name"] = data.pop("telemetry_device_name", data.get("serial_number"))
        devices.append(data)
    
    return MycoBrainDeviceListResponse(
        data=devices,
        pagination={"limit": pagination.limit, "offset": pagination.offset, "total": total},
    )


@devices_router.post("", response_model=MycoBrainDeviceResponse, status_code=status.HTTP_201_CREATED)
async def register_mycobrain_device(
    device: MycoBrainDeviceCreate,
    db: AsyncSession = Depends(get_db_session),
) -> MycoBrainDeviceResponse:
    """Register a new MycoBrain device."""
    
    # First, create the telemetry.device entry
    telemetry_stmt = text("""
        INSERT INTO telemetry.device (name, slug, status, taxon_id, location, metadata)
        VALUES (
            :name, 
            :slug,
            'active',
            :taxon_id,
            CASE WHEN :location IS NOT NULL 
                 THEN ST_SetSRID(ST_GeomFromGeoJSON(:location), 4326)::geography 
                 ELSE NULL END,
            :metadata
        )
        RETURNING id
    """)
    
    telemetry_result = await db.execute(telemetry_stmt, {
        "name": device.name,
        "slug": device.serial_number.lower().replace(" ", "-"),
        "taxon_id": str(device.taxon_id) if device.taxon_id else None,
        "location": json.dumps(device.location.model_dump()) if device.location else None,
        "metadata": json.dumps({"device_type": device.device_type.value}),
    })
    telemetry_device_id = telemetry_result.scalar_one()
    
    # Create the mycobrain.device entry
    mycobrain_stmt = text("""
        INSERT INTO mycobrain.device (
            telemetry_device_id,
            serial_number,
            device_type,
            hardware_revision,
            firmware_version_a,
            firmware_version_b,
            analog_channels,
            lora_dev_addr,
            lora_frequency_mhz,
            lora_spreading_factor,
            lora_bandwidth_khz,
            telemetry_interval_ms,
            location_name,
            purpose,
            metadata
        ) VALUES (
            :telemetry_device_id,
            :serial_number,
            :device_type::mycobrain.device_type,
            :hardware_revision,
            :firmware_version_a,
            :firmware_version_b,
            :analog_channels::jsonb,
            :lora_dev_addr,
            :lora_frequency_mhz,
            :lora_spreading_factor,
            :lora_bandwidth_khz,
            :telemetry_interval_ms,
            :location_name,
            :purpose,
            :metadata::jsonb
        )
        RETURNING *
    """)
    
    analog_config = device.analog_channels or {}
    lora = device.lora_config
    
    result = await db.execute(mycobrain_stmt, {
        "telemetry_device_id": str(telemetry_device_id),
        "serial_number": device.serial_number,
        "device_type": device.device_type.value,
        "hardware_revision": device.hardware_revision,
        "firmware_version_a": device.firmware_version_a,
        "firmware_version_b": device.firmware_version_b,
        "analog_channels": json.dumps({k: v.model_dump() for k, v in analog_config.items()}),
        "lora_dev_addr": lora.dev_addr if lora else None,
        "lora_frequency_mhz": lora.frequency_mhz if lora else None,
        "lora_spreading_factor": lora.spreading_factor if lora else None,
        "lora_bandwidth_khz": lora.bandwidth_khz if lora else None,
        "telemetry_interval_ms": device.telemetry_interval_ms,
        "location_name": device.location_name,
        "purpose": device.purpose,
        "metadata": json.dumps(device.metadata),
    })
    
    await db.commit()
    
    row = result.mappings().one()
    return _build_device_response(dict(row), device.name, device.location)


@devices_router.get("/{device_id}", response_model=MycoBrainDeviceResponse)
async def get_mycobrain_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db_session),
) -> MycoBrainDeviceResponse:
    """Get a MycoBrain device by ID."""
    stmt = text("""
        SELECT 
            d.*,
            td.name as telemetry_device_name,
            ST_AsGeoJSON(td.location::geometry) as location_geojson,
            CASE 
                WHEN d.last_seen_at > now() - interval '2 minutes' THEN 'online'
                WHEN d.last_seen_at > now() - interval '10 minutes' THEN 'stale'
                ELSE 'offline'
            END as connectivity_status,
            (SELECT count(*) FROM mycobrain.command_queue cq 
             WHERE cq.device_id = d.id AND cq.status = 'pending') as pending_commands
        FROM mycobrain.device d
        LEFT JOIN telemetry.device td ON td.id = d.telemetry_device_id
        WHERE d.id = :device_id
    """)
    
    result = await db.execute(stmt, {"device_id": str(device_id)})
    row = result.mappings().first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    
    data = dict(row)
    loc = data.pop("location_geojson", None)
    location = json.loads(loc) if loc else None
    name = data.pop("telemetry_device_name", data.get("serial_number"))
    
    return _build_device_response(data, name, location)


@devices_router.get("/serial/{serial_number}", response_model=MycoBrainDeviceResponse)
async def get_device_by_serial(
    serial_number: str,
    db: AsyncSession = Depends(get_db_session),
) -> MycoBrainDeviceResponse:
    """Get a MycoBrain device by serial number."""
    stmt = text("""
        SELECT 
            d.*,
            td.name as telemetry_device_name,
            ST_AsGeoJSON(td.location::geometry) as location_geojson,
            CASE 
                WHEN d.last_seen_at > now() - interval '2 minutes' THEN 'online'
                WHEN d.last_seen_at > now() - interval '10 minutes' THEN 'stale'
                ELSE 'offline'
            END as connectivity_status,
            (SELECT count(*) FROM mycobrain.command_queue cq 
             WHERE cq.device_id = d.id AND cq.status = 'pending') as pending_commands
        FROM mycobrain.device d
        LEFT JOIN telemetry.device td ON td.id = d.telemetry_device_id
        WHERE d.serial_number = :serial_number
    """)
    
    result = await db.execute(stmt, {"serial_number": serial_number})
    row = result.mappings().first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    
    data = dict(row)
    loc = data.pop("location_geojson", None)
    location = json.loads(loc) if loc else None
    name = data.pop("telemetry_device_name", data.get("serial_number"))
    
    return _build_device_response(data, name, location)


@devices_router.post("/{device_id}/api-key", response_model=DeviceAPIKeyResponse)
async def generate_device_api_key(
    device_id: UUID,
    db: AsyncSession = Depends(get_db_session),
) -> DeviceAPIKeyResponse:
    """Generate a new API key for a device."""
    # Generate a secure API key
    api_key = f"mcb_{secrets.token_urlsafe(32)}"
    api_key_hash = hashlib.sha256(api_key.encode()).digest()
    api_key_prefix = api_key[:12]
    
    stmt = text("""
        UPDATE mycobrain.device
        SET api_key_hash = :api_key_hash,
            api_key_prefix = :api_key_prefix,
            updated_at = now()
        WHERE id = :device_id
        RETURNING serial_number, created_at
    """)
    
    result = await db.execute(stmt, {
        "device_id": str(device_id),
        "api_key_hash": api_key_hash,
        "api_key_prefix": api_key_prefix,
    })
    
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    
    await db.commit()
    
    return DeviceAPIKeyResponse(
        device_id=device_id,
        serial_number=row["serial_number"],
        api_key=api_key,
        api_key_prefix=api_key_prefix,
        created_at=datetime.now(timezone.utc),
    )


@devices_router.get("/{device_id}/readings", response_model=LatestReadingsResponse)
async def get_latest_readings(
    device_id: UUID,
    db: AsyncSession = Depends(get_db_session),
) -> LatestReadingsResponse:
    """Get the latest sensor readings for a device."""
    stmt = text("""
        SELECT * FROM mycobrain.v_latest_readings
        WHERE device_id = :device_id
    """)
    
    result = await db.execute(stmt, {"device_id": str(device_id)})
    row = result.mappings().first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Get current MOSFET states
    mosfet_stmt = text("""
        SELECT mosfet_states FROM mycobrain.device WHERE id = :device_id
    """)
    mosfet_result = await db.execute(mosfet_stmt, {"device_id": str(device_id)})
    mosfet_row = mosfet_result.mappings().first()
    
    return LatestReadingsResponse(
        device_id=row["device_id"],
        serial_number=row["serial_number"],
        temperature_c=row.get("temperature_c"),
        humidity_percent=row.get("humidity_percent"),
        pressure_hpa=row.get("pressure_hpa"),
        gas_resistance_ohms=row.get("gas_resistance_ohms"),
        iaq_index=row.get("iaq_index"),
        bme_recorded_at=row.get("bme_recorded_at"),
        analog_voltages=row.get("analog_voltages") or {},
        mosfet_states=mosfet_row["mosfet_states"] if mosfet_row else {},
    )


# ============================================================================
# TELEMETRY INGESTION
# ============================================================================

@telemetry_router.post("/ingest", response_model=TelemetryIngestResponse)
async def ingest_telemetry(
    request: TelemetryIngestRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TelemetryIngestResponse:
    """
    Ingest telemetry from a MycoBrain device.
    
    Accepts both MDP-framed and NDJSON formatted telemetry.
    """
    # Resolve device
    device = await _resolve_device(db, request.device_id, request.serial_number)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    device_id = device["id"]
    telemetry_device_id = device["telemetry_device_id"]
    
    samples_created = 0
    streams_updated = []
    
    payload = request.payload
    recorded_at = request.recorded_at or datetime.now(timezone.utc)
    
    # Process BME688 readings
    if payload.bme688:
        bme = payload.bme688
        bme_stmt = text("""
            INSERT INTO mycobrain.bme688_reading (
                device_id, chip_id, i2c_address,
                temperature_c, humidity_percent, pressure_hpa,
                gas_resistance_ohms, iaq_index, altitude_m, dew_point_c,
                recorded_at, device_timestamp_ms
            ) VALUES (
                :device_id, :chip_id, :i2c_address,
                :temperature_c, :humidity_percent, :pressure_hpa,
                :gas_resistance_ohms, :iaq_index, :altitude_m, :dew_point_c,
                :recorded_at, :device_timestamp_ms
            )
        """)
        
        await db.execute(bme_stmt, {
            "device_id": str(device_id),
            "chip_id": bme.chip_id,
            "i2c_address": bme.i2c_address,
            "temperature_c": bme.temperature_c,
            "humidity_percent": bme.humidity_percent,
            "pressure_hpa": bme.pressure_hpa,
            "gas_resistance_ohms": bme.gas_resistance_ohms,
            "iaq_index": bme.iaq_index,
            "altitude_m": bme.altitude_m,
            "dew_point_c": bme.dew_point_c,
            "recorded_at": recorded_at,
            "device_timestamp_ms": request.device_timestamp_ms,
        })
        samples_created += 1
        streams_updated.append("bme688")
        
        # Also insert into telemetry.sample for unified view
        if telemetry_device_id:
            await _upsert_telemetry_samples(
                db, telemetry_device_id, recorded_at,
                {
                    "temperature": bme.temperature_c,
                    "humidity": bme.humidity_percent,
                    "pressure": bme.pressure_hpa,
                    "iaq": bme.iaq_index,
                }
            )
    
    # Process analog readings
    analog_readings = payload.analog or []
    
    # Also check flat format
    for i, attr in enumerate(["ai1_v", "ai2_v", "ai3_v", "ai4_v"], 1):
        val = getattr(payload, attr, None)
        if val is not None:
            from ..schemas.mycobrain import AnalogReading
            analog_readings.append(AnalogReading(channel=f"AI{i}", voltage=val))
    
    for reading in analog_readings:
        analog_stmt = text("""
            INSERT INTO mycobrain.analog_reading (
                device_id, channel, raw_adc_count, voltage,
                calibrated_value, calibrated_unit,
                recorded_at, device_timestamp_ms
            ) VALUES (
                :device_id, :channel, :raw_adc_count, :voltage,
                :calibrated_value, :calibrated_unit,
                :recorded_at, :device_timestamp_ms
            )
        """)
        
        await db.execute(analog_stmt, {
            "device_id": str(device_id),
            "channel": reading.channel,
            "raw_adc_count": reading.raw_adc_count,
            "voltage": reading.voltage,
            "calibrated_value": reading.calibrated_value,
            "calibrated_unit": reading.calibrated_unit,
            "recorded_at": recorded_at,
            "device_timestamp_ms": request.device_timestamp_ms,
        })
        samples_created += 1
        streams_updated.append(reading.channel)
    
    # Update MOSFET states
    if payload.mosfet_states:
        mosfet_stmt = text("""
            UPDATE mycobrain.device
            SET mosfet_states = mosfet_states || :states::jsonb,
                updated_at = now()
            WHERE id = :device_id
        """)
        await db.execute(mosfet_stmt, {
            "device_id": str(device_id),
            "states": json.dumps(payload.mosfet_states),
        })
    
    # Update power status
    if payload.usb_power is not None or payload.battery_v is not None:
        power_stmt = text("""
            UPDATE mycobrain.device
            SET usb_power_connected = COALESCE(:usb_power, usb_power_connected),
                battery_voltage = COALESCE(:battery_v, battery_voltage),
                updated_at = now()
            WHERE id = :device_id
        """)
        await db.execute(power_stmt, {
            "device_id": str(device_id),
            "usb_power": payload.usb_power,
            "battery_v": payload.battery_v,
        })
    
    # Update I2C addresses
    if payload.i2c_addresses:
        i2c_stmt = text("""
            UPDATE mycobrain.device
            SET i2c_addresses = :addresses::jsonb,
                updated_at = now()
            WHERE id = :device_id
        """)
        await db.execute(i2c_stmt, {
            "device_id": str(device_id),
            "addresses": json.dumps(payload.i2c_addresses),
        })
    
    # Update last seen and sequence
    seen_stmt = text("""
        UPDATE mycobrain.device
        SET last_seen_at = now(),
            last_sequence_number = COALESCE(:seq, last_sequence_number)
        WHERE id = :device_id
    """)
    await db.execute(seen_stmt, {
        "device_id": str(device_id),
        "seq": request.sequence_number,
    })
    
    await db.commit()
    
    # Publish to Mycorrhizae Protocol
    protocol = get_protocol()
    msg = MycorrhizaeMessage(
        channel=f"device.{device['serial_number']}",
        device_serial=device["serial_number"],
        source_type="device",
        source_id=str(device_id),
        message_type="telemetry",
        payload=payload.model_dump(exclude_none=True),
    )
    protocol.publish(msg)
    
    return TelemetryIngestResponse(
        success=True,
        device_id=device_id,
        samples_created=samples_created,
        streams_updated=list(set(streams_updated)),
    )


@telemetry_router.post("/ingest/batch", response_model=TelemetryBatchIngestResponse)
async def ingest_telemetry_batch(
    request: TelemetryBatchIngestRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TelemetryBatchIngestResponse:
    """Batch ingest telemetry from multiple readings or devices."""
    processed = 0
    failed = 0
    errors = []
    
    for i, item in enumerate(request.items):
        try:
            await ingest_telemetry(item, db)
            processed += 1
        except HTTPException as e:
            failed += 1
            errors.append({"index": i, "error": e.detail})
        except Exception as e:
            failed += 1
            errors.append({"index": i, "error": str(e)})
    
    return TelemetryBatchIngestResponse(
        success=failed == 0,
        total_items=len(request.items),
        items_processed=processed,
        items_failed=failed,
        errors=errors,
    )


# ============================================================================
# COMMAND QUEUE
# ============================================================================

@commands_router.post("", response_model=CommandResponse, status_code=status.HTTP_201_CREATED)
async def queue_command(
    request: CommandCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> CommandResponse:
    """Queue a command for a MycoBrain device."""
    device = await _resolve_device(db, request.device_id, request.serial_number)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    stmt = text("""
        INSERT INTO mycobrain.command_queue (
            device_id, command_type, command_payload, priority,
            scheduled_at, expires_at, max_retries, requested_by, metadata
        ) VALUES (
            :device_id, :command_type, :command_payload::jsonb, :priority,
            COALESCE(:scheduled_at, now()),
            COALESCE(:expires_at, now() + interval '1 hour'),
            :max_retries, :requested_by, :metadata::jsonb
        )
        RETURNING *
    """)
    
    result = await db.execute(stmt, {
        "device_id": str(device["id"]),
        "command_type": request.command_type,
        "command_payload": json.dumps(request.command_payload.model_dump()),
        "priority": request.priority,
        "scheduled_at": request.scheduled_at,
        "expires_at": request.expires_at,
        "max_retries": request.max_retries,
        "requested_by": request.requested_by,
        "metadata": json.dumps(request.metadata),
    })
    
    await db.commit()
    row = result.mappings().one()
    
    return _build_command_response(dict(row), device["serial_number"])


@commands_router.get("", response_model=CommandListResponse)
async def list_commands(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_db_session),
    device_id: Optional[UUID] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
) -> CommandListResponse:
    """List queued commands, optionally filtered by device or status."""
    stmt = text("""
        SELECT cq.*, d.serial_number
        FROM mycobrain.command_queue cq
        JOIN mycobrain.device d ON d.id = cq.device_id
        WHERE (:device_id IS NULL OR cq.device_id = :device_id::uuid)
          AND (:status IS NULL OR cq.status::text = :status)
        ORDER BY cq.priority ASC, cq.scheduled_at ASC
        LIMIT :limit OFFSET :offset
    """)
    
    count_stmt = text("""
        SELECT count(*)
        FROM mycobrain.command_queue cq
        WHERE (:device_id IS NULL OR cq.device_id = :device_id::uuid)
          AND (:status IS NULL OR cq.status::text = :status)
    """)
    
    params = {
        "device_id": str(device_id) if device_id else None,
        "status": status_filter,
        "limit": pagination.limit,
        "offset": pagination.offset,
    }
    
    result = await db.execute(stmt, params)
    count_result = await db.execute(count_stmt, params)
    total = count_result.scalar_one()
    
    commands = [
        _build_command_response(dict(row), row["serial_number"])
        for row in result.mappings().all()
    ]
    
    return CommandListResponse(
        data=commands,
        pagination={"limit": pagination.limit, "offset": pagination.offset, "total": total},
    )


@commands_router.get("/pending/{device_id}")
async def get_pending_commands(
    device_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(10, ge=1, le=100),
) -> List[CommandResponse]:
    """Get pending commands for a device (used by ingestion agents)."""
    stmt = text("""
        SELECT cq.*, d.serial_number
        FROM mycobrain.command_queue cq
        JOIN mycobrain.device d ON d.id = cq.device_id
        WHERE cq.device_id = :device_id
          AND cq.status = 'pending'
          AND cq.scheduled_at <= now()
          AND (cq.expires_at IS NULL OR cq.expires_at > now())
        ORDER BY cq.priority ASC, cq.scheduled_at ASC
        LIMIT :limit
    """)
    
    result = await db.execute(stmt, {"device_id": str(device_id), "limit": limit})
    
    return [
        _build_command_response(dict(row), row["serial_number"])
        for row in result.mappings().all()
    ]


@commands_router.post("/{command_id}/ack", response_model=CommandResponse)
async def acknowledge_command(
    command_id: UUID,
    request: CommandAckRequest,
    db: AsyncSession = Depends(get_db_session),
) -> CommandResponse:
    """Acknowledge a command as completed or failed."""
    new_status = CommandStatus.ACKNOWLEDGED if request.success else CommandStatus.FAILED
    
    stmt = text("""
        UPDATE mycobrain.command_queue
        SET status = :status::mycobrain.command_status,
            acked_at = now(),
            response_payload = :response::jsonb,
            error_message = :error
        WHERE id = :command_id
        RETURNING *, (SELECT serial_number FROM mycobrain.device WHERE id = device_id) as serial_number
    """)
    
    result = await db.execute(stmt, {
        "command_id": str(command_id),
        "status": new_status.value,
        "response": json.dumps(request.response_payload) if request.response_payload else None,
        "error": request.error_message,
    })
    
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Command not found")
    
    await db.commit()
    
    return _build_command_response(dict(row), row["serial_number"])


# Predefined command shortcuts
@commands_router.post("/{device_id}/mosfet", response_model=CommandResponse)
async def control_mosfet(
    device_id: UUID,
    request: MOSFETControlRequest,
    db: AsyncSession = Depends(get_db_session),
) -> CommandResponse:
    """Control a MOSFET output on the device."""
    cmd = CommandCreateRequest(
        device_id=device_id,
        command_type="mosfet_control",
        command_payload={"cmd": "mosfet", "target": request.mosfet, "value": request.state, "params": {}},
    )
    return await queue_command(cmd, db)


@commands_router.post("/{device_id}/telemetry-interval", response_model=CommandResponse)
async def set_telemetry_interval(
    device_id: UUID,
    request: TelemetryIntervalRequest,
    db: AsyncSession = Depends(get_db_session),
) -> CommandResponse:
    """Set the telemetry reporting interval."""
    cmd = CommandCreateRequest(
        device_id=device_id,
        command_type="set_interval",
        command_payload={"cmd": "set_interval", "value": request.interval_ms, "params": {}},
    )
    return await queue_command(cmd, db)


@commands_router.post("/{device_id}/i2c-scan", response_model=CommandResponse)
async def request_i2c_scan(
    device_id: UUID,
    _request: I2CScanRequest,
    db: AsyncSession = Depends(get_db_session),
) -> CommandResponse:
    """Request an I2C bus scan."""
    cmd = CommandCreateRequest(
        device_id=device_id,
        command_type="i2c_scan",
        command_payload={"cmd": "i2c_scan", "params": {}},
    )
    return await queue_command(cmd, db)


@commands_router.post("/{device_id}/reboot", response_model=CommandResponse)
async def reboot_device(
    device_id: UUID,
    request: RebootRequest,
    db: AsyncSession = Depends(get_db_session),
) -> CommandResponse:
    """Request a device reboot."""
    cmd = CommandCreateRequest(
        device_id=device_id,
        command_type="reboot",
        command_payload={"cmd": "reboot", "target": request.side, "value": request.delay_ms, "params": {}},
        priority=1,  # High priority
    )
    return await queue_command(cmd, db)


@commands_router.post("/{device_id}/firmware-update", response_model=CommandResponse)
async def request_firmware_update(
    device_id: UUID,
    request: FirmwareUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> CommandResponse:
    """Request a firmware update."""
    cmd = CommandCreateRequest(
        device_id=device_id,
        command_type="ota_update",
        command_payload={"cmd": "ota_update", "target": request.side, "value": request.url, "params": {"force": request.force}},
        priority=2,
        max_retries=1,  # Don't retry OTA
    )
    return await queue_command(cmd, db)


# ============================================================================
# MYCORRHIZAE PROTOCOL
# ============================================================================

@mycorrhizae_router.get("/channels")
async def list_mycorrhizae_channels() -> List[Dict[str, Any]]:
    """List all available Mycorrhizae Protocol channels."""
    protocol = get_protocol()
    return [ch.to_dict() for ch in protocol.list_channels()]


@mycorrhizae_router.post("/publish", response_model=MycorrhizaeMessageResponse)
async def publish_to_channel(
    request: MycorrhizaePublishRequest,
) -> MycorrhizaeMessageResponse:
    """Publish a message to a Mycorrhizae Protocol channel."""
    protocol = get_protocol()
    
    channel = protocol.get_channel(request.channel_name)
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel '{request.channel_name}' not found")
    
    msg = MycorrhizaeMessage(
        channel=request.channel_name,
        source_type=request.source_type,
        source_id=request.source_id,
        device_serial=request.device_serial,
        message_type=request.message_type,
        payload=request.payload,
        correlation_id=request.correlation_id,
        reply_to=request.reply_to,
        ttl_seconds=request.ttl_seconds,
    )
    
    protocol.publish(msg)
    
    return MycorrhizaeMessageResponse(
        id=msg.id,
        channel=msg.channel,
        timestamp=msg.timestamp,
        source_type=msg.source_type,
        source_id=msg.source_id,
        device_serial=msg.device_serial,
        message_type=msg.message_type,
        payload=msg.payload,
    )


@mycorrhizae_router.get("/channels/{channel_name}/messages")
async def get_channel_messages(
    channel_name: str,
    limit: int = Query(50, ge=1, le=200),
) -> List[MycorrhizaeMessageResponse]:
    """Get recent messages from a channel."""
    protocol = get_protocol()
    
    messages = protocol.get_recent_messages(channel_name, limit)
    
    return [
        MycorrhizaeMessageResponse(
            id=msg.id,
            channel=msg.channel,
            timestamp=msg.timestamp,
            source_type=msg.source_type,
            source_id=msg.source_id,
            device_serial=msg.device_serial,
            message_type=msg.message_type,
            payload=msg.payload,
        )
        for msg in messages
    ]


@mycorrhizae_router.get("/stream/{channel_name}")
async def stream_channel(
    channel_name: str,
) -> StreamingResponse:
    """
    Stream messages from a channel using Server-Sent Events (SSE).
    
    Connect with EventSource to receive real-time updates.
    """
    import asyncio
    from collections import deque
    
    protocol = get_protocol()
    channel = protocol.get_channel(channel_name)
    
    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_name}' not found")
    
    message_queue: deque = deque(maxlen=100)
    
    def on_message(msg: MycorrhizaeMessage) -> None:
        message_queue.append(msg)
    
    protocol.subscribe(channel_name, on_message)
    
    async def event_generator():
        try:
            while True:
                if message_queue:
                    msg = message_queue.popleft()
                    yield f"data: {msg.to_ndjson()}\n\n"
                else:
                    await asyncio.sleep(0.1)
        finally:
            protocol.unsubscribe(channel_name, on_message)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def _resolve_device(
    db: AsyncSession,
    device_id: Optional[UUID] = None,
    serial_number: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve a device by ID or serial number."""
    if device_id:
        stmt = text("SELECT * FROM mycobrain.device WHERE id = :id")
        result = await db.execute(stmt, {"id": str(device_id)})
    elif serial_number:
        stmt = text("SELECT * FROM mycobrain.device WHERE serial_number = :serial")
        result = await db.execute(stmt, {"serial": serial_number})
    else:
        return None
    
    row = result.mappings().first()
    return dict(row) if row else None


async def _upsert_telemetry_samples(
    db: AsyncSession,
    device_id: UUID,
    recorded_at: datetime,
    readings: Dict[str, Optional[float]],
) -> None:
    """Upsert readings into the unified telemetry.sample table."""
    for key, value in readings.items():
        if value is None:
            continue
        
        # Ensure stream exists
        stream_stmt = text("""
            INSERT INTO telemetry.stream (device_id, key)
            VALUES (:device_id, :key)
            ON CONFLICT (device_id, key) DO UPDATE SET updated_at = now()
            RETURNING id
        """)
        stream_result = await db.execute(stream_stmt, {
            "device_id": str(device_id),
            "key": key,
        })
        stream_id = stream_result.scalar_one()
        
        # Insert sample
        sample_stmt = text("""
            INSERT INTO telemetry.sample (stream_id, recorded_at, value_numeric)
            VALUES (:stream_id, :recorded_at, :value)
        """)
        await db.execute(sample_stmt, {
            "stream_id": str(stream_id),
            "recorded_at": recorded_at,
            "value": value,
        })


def _build_device_response(
    data: Dict[str, Any],
    name: str,
    location: Optional[Dict[str, Any]] = None,
) -> MycoBrainDeviceResponse:
    """Build a device response from database row."""
    return MycoBrainDeviceResponse(
        id=data["id"],
        telemetry_device_id=data.get("telemetry_device_id"),
        serial_number=data["serial_number"],
        device_type=data["device_type"],
        name=name or data["serial_number"],
        hardware_revision=data.get("hardware_revision"),
        firmware_version_a=data.get("firmware_version_a"),
        firmware_version_b=data.get("firmware_version_b"),
        firmware_updated_at=data.get("firmware_updated_at"),
        i2c_addresses=data.get("i2c_addresses", []),
        mosfet_states=data.get("mosfet_states", {}),
        usb_power_connected=data.get("usb_power_connected", False),
        battery_voltage=data.get("battery_voltage"),
        power_state=data.get("power_state", "unknown"),
        telemetry_interval_ms=data.get("telemetry_interval_ms", 5000),
        analog_channels=data.get("analog_channels", {}),
        lora_config=None,  # TODO: Reconstruct from individual fields
        location_name=data.get("location_name"),
        purpose=data.get("purpose"),
        location=location,
        taxon_id=data.get("taxon_id"),
        last_seen_at=data.get("last_seen_at"),
        last_sequence_number=data.get("last_sequence_number", 0),
        connectivity_status=ConnectivityStatus(data.get("connectivity_status", "offline")),
        pending_commands=data.get("pending_commands", 0),
        metadata=data.get("metadata", {}),
        created_at=data["created_at"],
        updated_at=data.get("updated_at"),
    )


def _build_command_response(
    data: Dict[str, Any],
    serial_number: str,
) -> CommandResponse:
    """Build a command response from database row."""
    payload = data.get("command_payload", {})
    if isinstance(payload, str):
        payload = json.loads(payload)
    
    from ..schemas.mycobrain import CommandPayload
    
    return CommandResponse(
        id=data["id"],
        device_id=data["device_id"],
        device_serial=serial_number,
        command_type=data["command_type"],
        command_payload=CommandPayload(**payload) if payload else CommandPayload(cmd="unknown"),
        priority=data["priority"],
        status=CommandStatus(data["status"]),
        retry_count=data.get("retry_count", 0),
        max_retries=data.get("max_retries", 3),
        sequence_number=data.get("sequence_number"),
        scheduled_at=data.get("scheduled_at", data["created_at"]),
        sent_at=data.get("sent_at"),
        acked_at=data.get("acked_at"),
        expires_at=data.get("expires_at"),
        response_payload=data.get("response_payload"),
        error_message=data.get("error_message"),
        requested_by=data.get("requested_by"),
        metadata=data.get("metadata", {}),
        created_at=data["created_at"],
        updated_at=data.get("updated_at"),
    )


# ============================================================================
# MOUNT SUB-ROUTERS
# ============================================================================

mycobrain_router.include_router(devices_router)
mycobrain_router.include_router(telemetry_router)
mycobrain_router.include_router(commands_router)
mycobrain_router.include_router(automation_router)
mycobrain_router.include_router(mycorrhizae_router)


