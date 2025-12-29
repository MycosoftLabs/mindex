"""
MycoBrain Device Schemas

Pydantic models for MycoBrain device registration, telemetry ingestion,
command queuing, and Mycorrhizae Protocol integration.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .common import GeoJSON, PaginationMeta, TimestampedModel


# ============================================================================
# ENUMS
# ============================================================================

class DeviceType(str, Enum):
    """Supported Mycosoft device types."""
    MYCOBRAIN_V1 = "mycobrain_v1"
    MUSHROOM_1 = "mushroom_1"
    SPOREBASE = "sporebase"
    CUSTOM_SENSOR = "custom_sensor"
    GATEWAY = "gateway"


class CommandStatus(str, Enum):
    """Command execution status."""
    PENDING = "pending"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ConnectivityStatus(str, Enum):
    """Device connectivity status."""
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"


class MDPMessageType(str, Enum):
    """MDP v1 message types."""
    TELEMETRY = "telemetry"
    COMMAND = "command"
    EVENT = "event"
    ACK = "ack"
    NACK = "nack"
    HEARTBEAT = "heartbeat"
    DISCOVERY = "discovery"


# ============================================================================
# DEVICE REGISTRATION & MANAGEMENT
# ============================================================================

class AnalogChannelConfig(BaseModel):
    """Configuration for an analog input channel."""
    label: str = "Channel"
    unit: str = "V"
    min_value: float = Field(0, alias="min")
    max_value: float = Field(3.3, alias="max")
    calibration_offset: float = 0.0
    calibration_scale: float = 1.0

    class Config:
        populate_by_name = True


class LoRaConfig(BaseModel):
    """LoRa radio configuration."""
    dev_addr: Optional[str] = None
    frequency_mhz: float = 915.0
    spreading_factor: int = Field(7, ge=6, le=12)
    bandwidth_khz: float = 125.0
    coding_rate: int = Field(5, ge=5, le=8)
    tx_power_dbm: int = Field(14, ge=2, le=20)


class MycoBrainDeviceCreate(BaseModel):
    """Request body for registering a new MycoBrain device."""
    serial_number: str = Field(..., min_length=6, max_length=32)
    device_type: DeviceType = DeviceType.MYCOBRAIN_V1
    name: str = Field(..., min_length=1, max_length=128)
    
    # Optional hardware info
    hardware_revision: Optional[str] = None
    firmware_version_a: Optional[str] = None
    firmware_version_b: Optional[str] = None
    
    # Location
    location_name: Optional[str] = None
    purpose: Optional[str] = None
    location: Optional[GeoJSON] = None
    
    # Configuration
    analog_channels: Optional[Dict[str, AnalogChannelConfig]] = None
    lora_config: Optional[LoRaConfig] = None
    telemetry_interval_ms: int = Field(5000, ge=100, le=3600000)
    
    # Taxon association (for cultivation tracking)
    taxon_id: Optional[UUID] = None
    
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MycoBrainDeviceUpdate(BaseModel):
    """Request body for updating a MycoBrain device."""
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    firmware_version_a: Optional[str] = None
    firmware_version_b: Optional[str] = None
    location_name: Optional[str] = None
    purpose: Optional[str] = None
    location: Optional[GeoJSON] = None
    analog_channels: Optional[Dict[str, AnalogChannelConfig]] = None
    lora_config: Optional[LoRaConfig] = None
    telemetry_interval_ms: Optional[int] = Field(None, ge=100, le=3600000)
    taxon_id: Optional[UUID] = None
    metadata: Optional[Dict[str, Any]] = None


class MycoBrainDeviceResponse(TimestampedModel):
    """Response model for a MycoBrain device."""
    id: UUID
    telemetry_device_id: Optional[UUID] = None
    serial_number: str
    device_type: DeviceType
    name: str
    
    # Hardware info
    hardware_revision: Optional[str] = None
    firmware_version_a: Optional[str] = None
    firmware_version_b: Optional[str] = None
    firmware_updated_at: Optional[datetime] = None
    
    # I2C sensors
    i2c_addresses: List[int] = Field(default_factory=list)
    
    # MOSFET states
    mosfet_states: Dict[str, bool] = Field(default_factory=dict)
    
    # Power status
    usb_power_connected: bool = False
    battery_voltage: Optional[float] = None
    power_state: str = "unknown"
    
    # Configuration
    telemetry_interval_ms: int = 5000
    analog_channels: Dict[str, AnalogChannelConfig] = Field(default_factory=dict)
    lora_config: Optional[LoRaConfig] = None
    
    # Location
    location_name: Optional[str] = None
    purpose: Optional[str] = None
    location: Optional[GeoJSON] = None
    taxon_id: Optional[UUID] = None
    
    # Status
    last_seen_at: Optional[datetime] = None
    last_sequence_number: int = 0
    connectivity_status: ConnectivityStatus = ConnectivityStatus.OFFLINE
    pending_commands: int = 0
    
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MycoBrainDeviceListResponse(BaseModel):
    """Paginated list of MycoBrain devices."""
    data: List[MycoBrainDeviceResponse]
    pagination: PaginationMeta


class DeviceAPIKeyResponse(BaseModel):
    """Response after generating a device API key."""
    device_id: UUID
    serial_number: str
    api_key: str = Field(..., description="Full API key (only shown once)")
    api_key_prefix: str = Field(..., description="First 8 characters for identification")
    created_at: datetime


# ============================================================================
# TELEMETRY INGESTION
# ============================================================================

class BME688Reading(BaseModel):
    """BME688 environmental sensor reading."""
    chip_id: Optional[str] = None
    i2c_address: Optional[int] = None
    temperature_c: Optional[float] = None
    humidity_percent: Optional[float] = None
    pressure_hpa: Optional[float] = None
    gas_resistance_ohms: Optional[float] = None
    iaq_index: Optional[float] = None
    altitude_m: Optional[float] = None
    dew_point_c: Optional[float] = None


class AnalogReading(BaseModel):
    """Analog channel reading."""
    channel: str = Field(..., pattern=r"^AI[1-4]$")
    raw_adc_count: Optional[int] = None
    voltage: float
    calibrated_value: Optional[float] = None
    calibrated_unit: Optional[str] = None


class TelemetryPayload(BaseModel):
    """Telemetry payload from a MycoBrain device."""
    # Environmental sensors
    bme688: Optional[BME688Reading] = None
    
    # Analog inputs
    analog: Optional[List[AnalogReading]] = None
    
    # Alternative flat format for analog
    ai1_v: Optional[float] = Field(None, alias="AI1")
    ai2_v: Optional[float] = Field(None, alias="AI2")
    ai3_v: Optional[float] = Field(None, alias="AI3")
    ai4_v: Optional[float] = Field(None, alias="AI4")
    
    # MOSFET states
    mosfet_states: Optional[Dict[str, bool]] = None
    
    # Power status
    usb_power: Optional[bool] = None
    battery_v: Optional[float] = None
    
    # I2C scan results
    i2c_addresses: Optional[List[int]] = None
    
    # Raw sensor data passthrough
    raw: Optional[Dict[str, Any]] = None

    class Config:
        populate_by_name = True


class TelemetryIngestRequest(BaseModel):
    """
    Request body for ingesting telemetry from a MycoBrain device.
    
    Supports both MDP-framed binary and NDJSON formats.
    """
    # Device identification (one required)
    device_id: Optional[UUID] = None
    serial_number: Optional[str] = None
    
    # MDP frame metadata
    sequence_number: Optional[int] = Field(None, ge=0, le=65535)
    message_type: MDPMessageType = MDPMessageType.TELEMETRY
    
    # Timestamps
    device_timestamp_ms: Optional[int] = None
    recorded_at: Optional[datetime] = None
    
    # Payload
    payload: TelemetryPayload
    
    # Raw frame (for debugging)
    raw_cobs_frame: Optional[str] = Field(None, description="Base64-encoded COBS frame")
    crc_valid: bool = True
    
    @field_validator("recorded_at", mode="before")
    @classmethod
    def default_recorded_at(cls, v: Any) -> datetime:
        return v or datetime.utcnow()


class TelemetryIngestResponse(BaseModel):
    """Response after ingesting telemetry."""
    success: bool
    device_id: UUID
    samples_created: int
    streams_updated: List[str]
    message: Optional[str] = None


class TelemetryBatchIngestRequest(BaseModel):
    """Batch telemetry ingestion request."""
    items: List[TelemetryIngestRequest] = Field(..., max_length=1000)


class TelemetryBatchIngestResponse(BaseModel):
    """Response after batch telemetry ingestion."""
    success: bool
    total_items: int
    items_processed: int
    items_failed: int
    errors: List[Dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# COMMAND QUEUE
# ============================================================================

class CommandPayload(BaseModel):
    """Generic command payload."""
    cmd: str
    target: Optional[str] = None
    value: Optional[Union[bool, int, float, str, Dict[str, Any]]] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class CommandCreateRequest(BaseModel):
    """Request to queue a command for a device."""
    device_id: Optional[UUID] = None
    serial_number: Optional[str] = None
    
    command_type: str = Field(..., min_length=1, max_length=64)
    command_payload: CommandPayload
    
    priority: int = Field(5, ge=1, le=10, description="1=highest, 10=lowest")
    scheduled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    max_retries: int = Field(3, ge=0, le=10)
    
    requested_by: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CommandResponse(TimestampedModel):
    """Response model for a queued command."""
    id: UUID
    device_id: UUID
    device_serial: str
    
    command_type: str
    command_payload: CommandPayload
    priority: int
    
    status: CommandStatus
    retry_count: int
    max_retries: int
    
    sequence_number: Optional[int] = None
    scheduled_at: datetime
    sent_at: Optional[datetime] = None
    acked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    
    response_payload: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    
    requested_by: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CommandListResponse(BaseModel):
    """Paginated list of commands."""
    data: List[CommandResponse]
    pagination: PaginationMeta


class CommandAckRequest(BaseModel):
    """Request to acknowledge a command."""
    command_id: UUID
    success: bool
    response_payload: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


# ============================================================================
# PREDEFINED COMMANDS
# ============================================================================

class MOSFETControlRequest(BaseModel):
    """Control a MOSFET output."""
    mosfet: str = Field(..., pattern=r"^M[1-4]$")
    state: bool


class TelemetryIntervalRequest(BaseModel):
    """Set telemetry interval."""
    interval_ms: int = Field(..., ge=100, le=3600000)


class I2CScanRequest(BaseModel):
    """Request an I2C bus scan."""
    pass  # No parameters needed


class FirmwareUpdateRequest(BaseModel):
    """Request a firmware update."""
    url: str
    side: str = Field("A", pattern=r"^[AB]$")
    force: bool = False


class RebootRequest(BaseModel):
    """Request a device reboot."""
    side: str = Field("A", pattern=r"^[AB]$")
    delay_ms: int = Field(0, ge=0, le=60000)


# ============================================================================
# NATUREOS / MYCORRHIZAE INTEGRATION
# ============================================================================

class NatureOSWidgetConfig(BaseModel):
    """NatureOS widget configuration for a device."""
    device_id: UUID
    widget_type: str = "mycobrain_dashboard"
    display_name: str
    
    # Layout configuration
    layout_config: Dict[str, Any] = Field(default_factory=dict)
    
    # Data binding
    bound_streams: List[str] = Field(default_factory=list)
    refresh_interval_ms: int = Field(5000, ge=1000, le=300000)
    
    # Access control
    visibility: str = Field("private", pattern=r"^(private|shared|public)$")
    shared_with: List[UUID] = Field(default_factory=list)


class MycorrhizaeChannelConfig(BaseModel):
    """Mycorrhizae Protocol channel configuration."""
    channel_name: str
    channel_type: str = Field("device", pattern=r"^(device|aggregate|computed)$")
    
    # Source binding
    device_id: Optional[UUID] = None
    stream_pattern: Optional[str] = None
    
    # Subscriber configuration
    subscriber_type: str = Field("natureos", pattern=r"^(natureos|mas_agent|external)$")
    subscriber_endpoint: Optional[str] = None
    subscriber_config: Dict[str, Any] = Field(default_factory=dict)
    
    # Protocol settings
    protocol_version: str = "v1"
    format: str = Field("ndjson", pattern=r"^(ndjson|cbor|protobuf)$")


class MycorrhizaePublishRequest(BaseModel):
    """Request to publish a message to a Mycorrhizae channel."""
    channel_name: str
    message_type: str = "telemetry"
    
    source_type: str = "mindex"
    source_id: Optional[str] = None
    device_serial: Optional[str] = None
    
    payload: Dict[str, Any]
    
    correlation_id: Optional[UUID] = None
    reply_to: Optional[str] = None
    ttl_seconds: int = Field(3600, ge=60, le=86400)


class MycorrhizaeMessageResponse(BaseModel):
    """A message from a Mycorrhizae channel."""
    id: UUID
    channel: str
    timestamp: datetime
    
    source_type: str
    source_id: Optional[str] = None
    device_serial: Optional[str] = None
    
    message_type: str
    payload: Dict[str, Any]


# ============================================================================
# AUTOMATION RULES
# ============================================================================

class AutomationRuleCreate(BaseModel):
    """Create an automation rule."""
    device_id: UUID
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    enabled: bool = True
    
    # Trigger condition
    trigger_stream: str
    trigger_operator: str = Field(..., pattern=r"^(gt|lt|gte|lte|eq|neq|between)$")
    trigger_value: float
    trigger_value_high: Optional[float] = None  # For 'between'
    trigger_duration_ms: int = Field(0, ge=0)
    
    # Action
    action_type: str = Field(..., pattern=r"^(mosfet_on|mosfet_off|mosfet_toggle|alert|webhook|command)$")
    action_target: Optional[str] = None
    action_payload: Dict[str, Any] = Field(default_factory=dict)
    
    # Cooldown
    cooldown_ms: int = Field(60000, ge=0)


class AutomationRuleResponse(TimestampedModel):
    """Response model for an automation rule."""
    id: UUID
    device_id: UUID
    name: str
    description: Optional[str] = None
    enabled: bool
    
    trigger_stream: str
    trigger_operator: str
    trigger_value: float
    trigger_value_high: Optional[float] = None
    trigger_duration_ms: int
    
    action_type: str
    action_target: Optional[str] = None
    action_payload: Dict[str, Any]
    
    cooldown_ms: int
    last_triggered_at: Optional[datetime] = None
    trigger_count: int = 0


# ============================================================================
# LATEST READINGS
# ============================================================================

class LatestReadingsResponse(BaseModel):
    """Latest sensor readings for a device."""
    device_id: UUID
    serial_number: str
    
    # Environmental
    temperature_c: Optional[float] = None
    humidity_percent: Optional[float] = None
    pressure_hpa: Optional[float] = None
    gas_resistance_ohms: Optional[float] = None
    iaq_index: Optional[float] = None
    bme_recorded_at: Optional[datetime] = None
    
    # Analog voltages
    analog_voltages: Dict[str, float] = Field(default_factory=dict)
    analog_recorded_at: Optional[datetime] = None
    
    # MOSFET states
    mosfet_states: Dict[str, bool] = Field(default_factory=dict)
    
    # Power
    usb_power_connected: bool = False
    battery_voltage: Optional[float] = None


