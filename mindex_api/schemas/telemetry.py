from __future__ import annotations

from datetime import datetime
from typing import List, Optional
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
