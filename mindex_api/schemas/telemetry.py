from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .common import GeoJSON, PaginationMeta, TimestampedModel


class DeviceBase(TimestampedModel):
    id: UUID
    name: str
    slug: Optional[str] = None
    status: str
    taxon_id: Optional[UUID] = None
    metadata: dict = Field(default_factory=dict)
    location: Optional[GeoJSON] = None


class DeviceListResponse(BaseModel):
    data: List[DeviceBase]
    pagination: PaginationMeta


class DeviceLatestSample(BaseModel):
    device_id: UUID
    device_name: str
    device_slug: Optional[str]
    stream_id: UUID
    stream_key: str
    stream_unit: Optional[str] = None
    sample_id: UUID
    recorded_at: datetime
    value_numeric: Optional[float] = None
    value_text: Optional[str] = None
    value_json: Optional[dict] = None
    value_unit: Optional[str] = None
    sample_location: Optional[GeoJSON] = None
    device_location: Optional[GeoJSON] = None
    sample_metadata: dict = Field(default_factory=dict)


class DeviceLatestSamplesResponse(BaseModel):
    data: List[DeviceLatestSample]
    pagination: PaginationMeta


class EnvelopeIngestRequest(BaseModel):
    """Ingest a unified envelope and expand into telemetry.sample rows."""
    envelope: Dict[str, Any]
    verified_by: Optional[str] = None


class EnvelopeIngestResponse(BaseModel):
    success: bool
    device_slug: str
    envelope_msg_id: str
    envelope_seq: int
    samples_inserted: int
    samples_deduped: int
    recorded_at: datetime
    verification: dict = Field(default_factory=dict)


class ReplayStartRequest(BaseModel):
    device_slug: str
    stream_key: Optional[str] = None
    replay_type: str = "time_range"
    start_time: datetime
    end_time: Optional[datetime] = None
    playback_speed: float = 1.0
    filters: dict = Field(default_factory=dict)
    created_by: Optional[str] = None


class ReplayState(BaseModel):
    id: UUID
    device_id: UUID
    stream_id: Optional[UUID] = None
    replay_type: str
    start_time: datetime
    end_time: Optional[datetime] = None
    current_position: datetime
    playback_speed: float
    is_playing: bool
    is_paused: bool
    filters: dict = Field(default_factory=dict)
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ReplayUpdateRequest(BaseModel):
    current_position: Optional[datetime] = None
    playback_speed: Optional[float] = None
    is_playing: Optional[bool] = None
    is_paused: Optional[bool] = None
    filters: Optional[dict] = None


class DeviceHealthStateCreate(BaseModel):
    device_slug: str
    status: str
    health_score: Optional[float] = None
    metrics: dict = Field(default_factory=dict)
    alerts: list = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    recorded_at: Optional[datetime] = None


class DeviceHealthState(BaseModel):
    id: UUID
    device_id: UUID
    recorded_at: datetime
    status: str
    health_score: Optional[float] = None
    metrics: dict = Field(default_factory=dict)
    alerts: list = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class TelemetrySampleRow(BaseModel):
    stream_key: str
    recorded_at: datetime
    value_numeric: Optional[float] = None
    value_json: Optional[dict] = None
    value_unit: Optional[str] = None
    verified: bool = False
    envelope_seq: Optional[int] = None
    envelope_msg_id: Optional[str] = None
