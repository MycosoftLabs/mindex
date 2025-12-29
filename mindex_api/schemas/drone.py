"""MycoDRONE Pydantic schemas for MINDEX API."""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from uuid import UUID


class DroneCreate(BaseModel):
    """Create drone registry entry."""
    device_id: UUID
    drone_type: str = Field(..., max_length=32)
    max_payload_kg: float = Field(..., gt=0)
    max_range_km: float = Field(..., gt=0)
    home_latitude: float = Field(..., ge=-90, le=90)
    home_longitude: float = Field(..., ge=-180, le=180)
    dock_id: Optional[UUID] = None


class DroneResponse(BaseModel):
    """Drone registry response."""
    id: UUID
    device_id: UUID
    drone_type: str
    max_payload_kg: float
    max_range_km: float
    home_latitude: float
    home_longitude: float
    dock_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class DroneMissionCreate(BaseModel):
    """Create drone mission."""
    drone_id: UUID
    mission_type: str = Field(..., pattern="^(deploy|retrieve|data_mule)$")
    target_device_id: Optional[UUID] = None
    waypoint_lat: Optional[float] = Field(None, ge=-90, le=90)
    waypoint_lon: Optional[float] = Field(None, ge=-180, le=180)
    waypoint_alt: Optional[float] = Field(None, gt=0)


class DroneMissionResponse(BaseModel):
    """Drone mission response."""
    id: UUID
    drone_id: UUID
    mission_type: str
    target_device_id: Optional[UUID]
    waypoint_lat: Optional[float]
    waypoint_lon: Optional[float]
    waypoint_alt: Optional[float]
    status: str
    progress: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime


class DroneTelemetryIngest(BaseModel):
    """Drone telemetry ingestion."""
    drone_id: UUID
    timestamp: datetime
    latitude: float
    longitude: float
    altitude_msl: float
    altitude_rel: float
    heading: float
    ground_speed: float
    battery_percent: int = Field(..., ge=0, le=100)
    battery_voltage: float
    flight_mode: str
    mission_state: str
    payload_latched: bool
    payload_type: Optional[str] = None
    temp_c: Optional[float] = None
    humidity_rh: Optional[float] = None


class DroneStatusResponse(BaseModel):
    """Drone status response."""
    drone_id: UUID
    drone_name: str
    drone_type: str
    max_payload_kg: float
    home_latitude: float
    home_longitude: float
    current_latitude: Optional[float]
    current_longitude: Optional[float]
    current_altitude: Optional[float]
    battery_percent: Optional[int]
    flight_mode: Optional[str]
    mission_state: Optional[str]
    payload_latched: Optional[bool]
    payload_type: Optional[str]
    active_mission_status: Optional[str]
    active_mission_progress: Optional[int]
    last_telemetry_time: Optional[datetime]


class DockCreate(BaseModel):
    """Create docking station."""
    name: str = Field(..., max_length=64)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    altitude: Optional[float] = None
    fiducial_id: Optional[str] = Field(None, max_length=32)
    charging_bays: int = Field(0, ge=0)


class DockResponse(BaseModel):
    """Docking station response."""
    id: UUID
    name: str
    latitude: float
    longitude: float
    altitude: Optional[float]
    fiducial_id: Optional[str]
    charging_bays: int
    created_at: datetime
    updated_at: datetime

