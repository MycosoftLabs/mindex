"""
Worldview API Response Envelope — standard wrapper for all Worldview responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Generic, List, Optional, TypeVar
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
    avani: Optional[Dict[str, Any]] = None


class WorldviewResponse(BaseModel):
    """Standard envelope for all Worldview API responses."""
    data: Any
    meta: WorldviewMeta = Field(default_factory=WorldviewMeta)


def wrap_response(
    data: Any,
    count: Optional[int] = None,
    cached: bool = False,
    plan: Optional[str] = None,
    avani: Optional[Dict[str, Any]] = None,
) -> dict:
    """Wrap data in the standard Worldview response envelope."""
    if count is None:
        count = len(data) if isinstance(data, list) else 1

    meta = {
        "request_id": str(uuid4()),
        "api_version": "v1",
        "count": count,
        "cached": cached,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "plan": plan,
    }
    if avani is not None:
        meta["avani"] = avani
    return {
        "data": data,
        "meta": meta,
    }


async def wrap_governed_response(
    *,
    data: Any,
    caller: Any,
    source_domains: List[str],
    count: Optional[int] = None,
    cached: bool = False,
    region: Optional[Dict[str, Any]] = None,
    time_window: Optional[Dict[str, Any]] = None,
) -> dict:
    """Wrap response data after AVANI governance review.

    The existing data payload remains unchanged; AVANI metadata is attached
    only under meta.avani for backward-compatible Worldview clients.
    """
    request_id = str(uuid4())
    from .avani_gateway import review_worldview_response
    from .snapshots import get_latest_snapshot_meta

    snapshot_meta = await get_latest_snapshot_meta(region=region)

    avani = await review_worldview_response(
        worldview_request_id=request_id,
        data=data,
        source_domains=source_domains,
        caller=caller,
        region=region,
        time_window=time_window,
        snapshot_meta=snapshot_meta,
    )
    response = wrap_response(
        data=data,
        count=count,
        cached=cached,
        plan=getattr(caller, "plan", None),
        avani=avani,
    )
    response["meta"]["request_id"] = request_id
    return response
