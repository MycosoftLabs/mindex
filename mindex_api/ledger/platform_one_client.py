"""Platform One HTTP ping — uses P1_BASE_URL + P1_API_KEY from settings."""
from __future__ import annotations

from typing import Any, Optional

import httpx

from ..config import settings


async def ping_platform_one(timeout: float = 4.0) -> dict[str, Any]:
    base = str(settings.p1_base_url) if settings.p1_base_url else None
    out: dict[str, Any] = {
        "configured": bool(base and settings.p1_api_key),
        "reachable": False,
        "base_url": base,
        "status_code": None,
    }
    if not base:
        return out
    try:
        headers = {}
        if settings.p1_api_key:
            headers["Authorization"] = f"Bearer {settings.p1_api_key}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(base.rstrip("/") + "/health", headers=headers)
            out["status_code"] = r.status_code
            out["reachable"] = r.status_code < 500
    except Exception:
        out["reachable"] = False
    return out
