from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(..., description="Overall API status.")
    db: str = Field(..., description="Database connectivity state.")
    timestamp: datetime = Field(..., description="UTC timestamp for the check.")
    service: str = Field("mindex", description="Name of the service.")
    version: str = Field("0.1.0", description="Service version.")
    git_sha: Optional[str] = Field(None, description="Git commit SHA if available.")


class VersionResponse(BaseModel):
    service: str = Field(..., description="Service name.")
    version: str = Field(..., description="Service version.")
    git_sha: Optional[str] = Field(None, description="Git commit SHA if available.")
