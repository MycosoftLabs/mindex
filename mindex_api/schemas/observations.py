from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .common import GeoJSON, PaginationMeta


class Observation(BaseModel):
    id: UUID
    taxon_id: Optional[UUID] = None
    source: str
    source_id: Optional[str] = None
    observer: Optional[str] = None
    observed_at: datetime
    accuracy_m: Optional[float] = Field(None, ge=0)
    media: List[dict] = Field(default_factory=list)
    notes: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    location: Optional[GeoJSON] = None


class ObservationListResponse(BaseModel):
    data: List[Observation]
    pagination: PaginationMeta
