from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(..., description="Overall API status.")
    db: str = Field(..., description="Database connectivity state.")
    timestamp: datetime = Field(..., description="UTC timestamp for the check.")
