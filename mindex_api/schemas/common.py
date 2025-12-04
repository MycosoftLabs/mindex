from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class PaginationMeta(BaseModel):
    limit: int = Field(..., ge=1, description="Page size.")
    offset: int = Field(..., ge=0, description="Offset into the result set.")
    total: Optional[int] = Field(
        None,
        ge=0,
        description="Optional total count if available.",
    )


class TimestampedModel(BaseModel):
    created_at: datetime
    updated_at: Optional[datetime] = None


class GeoJSON(BaseModel):
    type: str
    coordinates: Any
    properties: Optional[Dict[str, Any]] = None

    class Config:
        extra = "allow"
