"""
Worldview API Response Envelope — standard wrapper for all Worldview responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, List, Optional, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field

T = TypeVar("T")


class WorldviewMeta(BaseModel):
    """Metadata included with every Worldview API response."""
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    api_version: str = "v1"
    count: int = 0
    cached: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    plan: Optional[str] = None
    rate_limit_remaining_minute: Optional[int] = None
    rate_limit_remaining_day: Optional[int] = None


class WorldviewResponse(BaseModel):
    """Standard envelope for all Worldview API responses."""
    data: Any
    meta: WorldviewMeta = Field(default_factory=WorldviewMeta)


def wrap_response(
    data: Any,
    count: Optional[int] = None,
    cached: bool = False,
    plan: Optional[str] = None,
) -> dict:
    """Wrap data in the standard Worldview response envelope."""
    if count is None:
        count = len(data) if isinstance(data, list) else 1

    return {
        "data": data,
        "meta": {
            "request_id": str(uuid4()),
            "api_version": "v1",
            "count": count,
            "cached": cached,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "plan": plan,
        },
    }
