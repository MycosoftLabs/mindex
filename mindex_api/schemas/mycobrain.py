from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class MycoBrainDeviceCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    serial_number: str
    firmware_version: Optional[str] = None
    side_a_firmware_version: Optional[str] = None
    side_b_firmware_version: Optional[str] = None
    power_status: str = "unknown"
    i2c_addresses: List[str] = Field(default_factory=list)
    analog_channels: Dict[str, str] = Field(default_factory=dict)
    mosfet_states: Dict[str, bool] = Field(default_factory=dict)
    telemetry_interval_seconds: int = 60
    api_key: Optional[str] = None


class MycoBrainDeviceResponse(BaseModel):
    id: UUID
    name: str
    slug: Optional[str]
    status: str
    serial_number: str
    firmware_version: Optional[str]
    power_status: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class MDPTelemetryPayload(BaseModel):
    mdp_sequence_number: int = Field(..., ge=0)
    mdp_timestamp: datetime
    device_serial_number: str

    bme688_temperature: Optional[float] = None
    bme688_humidity: Optional[float] = None
    bme688_pressure: Optional[float] = None
    bme688_gas_resistance: Optional[float] = None

    ai1_voltage: Optional[float] = None
    ai2_voltage: Optional[float] = None
    ai3_voltage: Optional[float] = None
    ai4_voltage: Optional[float] = None

    mosfet_states: Dict[str, bool] = Field(default_factory=dict)
    power_status: Optional[str] = None
    i2c_addresses: Optional[List[str]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MDPTelemetryIngestionRequest(BaseModel):
    telemetry: MDPTelemetryPayload
    api_key: Optional[str] = None


class MDPTelemetryIngestionResponse(BaseModel):
    success: bool
    device_id: Optional[UUID] = None
    samples_created: int = 0
    duplicate: bool = False
    message: str


class DeviceCommandCreate(BaseModel):
    command_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    expires_at: Optional[datetime] = None


class DeviceCommandResponse(BaseModel):
    id: UUID
    device_id: UUID
    command_type: str
    command_id: str
    status: str
    payload: Dict[str, Any]
    priority: int
    created_at: datetime
    expires_at: Optional[datetime] = None


class MycoBrainStatusResponse(BaseModel):
    device_id: UUID
    device_name: str
    device_slug: Optional[str]
    device_status: str
    serial_number: Optional[str]
    firmware_version: Optional[str]
    power_status: Optional[str]
    last_seen_at: Optional[datetime]
    mdp_last_telemetry_at: Optional[datetime]
    pending_commands_count: Optional[int]
    seconds_since_last_telemetry: Optional[float]
