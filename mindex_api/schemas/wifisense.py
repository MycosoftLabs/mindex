"""WiFi Sense Pydantic schemas for MINDEX API."""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from uuid import UUID


class WiFiSenseDeviceCreate(BaseModel):
    """Create WiFi Sense device configuration."""
    device_id: UUID
    link_id: str = Field(..., max_length=32)
    channel: Optional[int] = Field(None, ge=1, le=14)
    bandwidth: Optional[int] = Field(None, ge=20, le=80)
    csi_format: Optional[int] = Field(None, ge=0, le=1)
    num_antennas: Optional[int] = Field(None, ge=1, le=8)
    num_subcarriers: Optional[int] = Field(None, ge=1, le=256)
    calibration_data: Optional[Dict[str, Any]] = None


class WiFiSenseDeviceResponse(BaseModel):
    """WiFi Sense device configuration response."""
    id: UUID
    device_id: UUID
    link_id: str
    channel: Optional[int]
    bandwidth: Optional[int]
    csi_format: Optional[int]
    num_antennas: Optional[int]
    num_subcarriers: Optional[int]
    calibration_data: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class WiFiSenseCSIIngest(BaseModel):
    """WiFi Sense CSI data ingestion."""
    device_id: UUID
    link_id: str
    timestamp_ns: int
    channel: int
    rssi: int
    csi_data: bytes
    csi_length: int
    csi_format: int
    num_subcarriers: int
    num_antennas: int


class WiFiSensePresenceEvent(BaseModel):
    """WiFi Sense presence event."""
    zone_id: str
    timestamp: datetime
    presence_type: str  # 'occupancy', 'motion', 'activity'
    confidence: float
    metadata: Optional[Dict[str, Any]] = None


class WiFiSenseTrack(BaseModel):
    """WiFi Sense track (multi-target)."""
    track_id: str
    zone_id: str
    position: Optional[Dict[str, float]] = None  # {lat, lon}
    velocity: Optional[float] = None
    activity_class: Optional[str] = None
    confidence: float
    first_seen: datetime
    last_seen: datetime


class WiFiSenseStatusResponse(BaseModel):
    """WiFi Sense status response."""
    device_id: UUID
    device_name: str
    link_id: str
    channel: Optional[int]
    bandwidth: Optional[int]
    csi_samples_count: int
    last_csi_timestamp: Optional[int]
    presence_events_count: int
    last_presence_timestamp: Optional[datetime]

