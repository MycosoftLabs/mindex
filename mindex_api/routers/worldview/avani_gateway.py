"""AVANI governance gateway for customer-facing Worldview responses."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from ...config import settings

INTERNAL_WORLDVIEW_DOMAINS = frozenset({"devices", "telemetry", "raw_telemetry", "device_commands"})
SENSITIVE_DOMAIN_HINTS = frozenset(
    {
        "military_installations",
        "fusarium_tracks",
        "fusarium_correlations",
        "crep_entities",
        "infrastructure",
        "power_grid",
        "water_systems",
        "internet_cables",
    }
)
ECOLOGICAL_DOMAIN_HINTS = frozenset(
    {
        "taxa",
        "species",
        "observations",
        "genetics",
        "buoys",
        "stream_gauges",
        "wildfires",
        "floods",
        "storms",
        "weather",
        "air_quality",
        "greenhouse_gas",
        "remote_sensing",
        "maritime",
    }
)


def _caller_to_dict(caller: Any | None) -> Dict[str, Any]:
    if caller is None:
        return {}
    return {
        "key_id": str(caller.key_id),
        "owner_id": str(caller.owner_id),
        "user_type": caller.user_type,
        "plan": caller.plan,
        "scopes": caller.scopes,
        "service": caller.service,
    }


def _count_items(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("results", "items", "data"):
            if isinstance(data.get(key), list):
                return len(data[key])
        return 1
    return 1 if data is not None else 0


def _local_degraded_review(
    *,
    worldview_request_id: str,
    data: Any,
    source_domains: List[str],
    caller: Any | None,
    reason: str,
    snapshot_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    domains = [d for d in source_domains if d]
    domain_set = set(domains)
    verdict = "allow"
    sensitivity = "public"
    notes = [reason]
    confidence = 0.45

    if domain_set & INTERNAL_WORLDVIEW_DOMAINS:
        verdict = "deny"
        sensitivity = "internal"
        confidence = 0.95
        notes.append("Internal telemetry/device domain requested; release denied locally.")
    elif domain_set & SENSITIVE_DOMAIN_HINTS:
        verdict = "allow_with_audit"
        sensitivity = "sensitive"
        notes.append("Sensitive domain released with degraded AVANI metadata.")

    ecological_risk = 0.0
    if domain_set & ECOLOGICAL_DOMAIN_HINTS:
        ecological_risk = min(0.8, 0.1 + (_count_items(data) / 1000.0))
        notes.append("Ecological domain marked for audit review.")

    return {
        "worldview_request_id": worldview_request_id,
        "worldstate_snapshot_id": (snapshot_meta or {}).get("worldstate_snapshot_id"),
        "source_domains": domains,
        "freshness": (snapshot_meta or {}).get("freshness") or "degraded",
        "degraded": True,
        "confidence": round(min(confidence, float((snapshot_meta or {}).get("confidence") or confidence)), 3),
        "provenance": {
            "source": "mindex_worldview_local_guard",
            "caller_plan": caller.plan if caller else None,
            "caller_type": caller.user_type if caller else None,
            "result_count": _count_items(data),
            "worldstate_snapshot": (snapshot_meta or {}).get("provenance"),
        },
        "sensitivity": sensitivity,
        "ecological_risk": round(ecological_risk, 3),
        "avani_verdict": verdict,
        "governance_notes": notes,
        "audit_trail_id": (snapshot_meta or {}).get("audit_trail_id"),
    }


async def review_worldview_response(
    *,
    worldview_request_id: str,
    data: Any,
    source_domains: List[str],
    caller: Any | None,
    region: Optional[Dict[str, Any]] = None,
    time_window: Optional[Dict[str, Any]] = None,
    snapshot_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Ask AVANI to review a filtered Worldview response, with explicit degraded fallback."""
    base_url = (settings.avani_api_url or "").rstrip("/")
    if not base_url:
        return _local_degraded_review(
            worldview_request_id=worldview_request_id,
            data=data,
            source_domains=source_domains,
            caller=caller,
            reason="AVANI_API_URL is not configured; local degraded Worldview guard applied.",
            snapshot_meta=snapshot_meta,
        )

    headers: Dict[str, str] = {}
    if settings.avani_api_key:
        headers["X-API-Key"] = settings.avani_api_key

    payload = {
        "worldview_request_id": worldview_request_id,
        "data": data,
        "source_domains": source_domains,
        "caller": _caller_to_dict(caller),
        "region": region,
        "time_window": time_window,
        "worldstate_snapshot_id": (snapshot_meta or {}).get("worldstate_snapshot_id"),
        "worldstate_degraded": bool((snapshot_meta or {}).get("degraded", True)),
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(
                f"{base_url}/api/avani/worldview/review",
                json=payload,
                headers=headers or None,
            )
            response.raise_for_status()
            reviewed = response.json()
            if snapshot_meta:
                reviewed.setdefault("worldstate_snapshot_id", snapshot_meta.get("worldstate_snapshot_id"))
                reviewed.setdefault("freshness", snapshot_meta.get("freshness"))
                reviewed["degraded"] = bool(reviewed.get("degraded") or snapshot_meta.get("degraded"))
                reviewed.setdefault("audit_trail_id", snapshot_meta.get("audit_trail_id"))
                reviewed["confidence"] = min(
                    float(reviewed.get("confidence") or 1.0),
                    float(snapshot_meta.get("confidence") or 1.0),
                )
                provenance = reviewed.get("provenance") if isinstance(reviewed.get("provenance"), dict) else {}
                reviewed["provenance"] = {**provenance, "worldstate_snapshot": snapshot_meta.get("provenance")}
            return reviewed
    except Exception as exc:
        return _local_degraded_review(
            worldview_request_id=worldview_request_id,
            data=data,
            source_domains=source_domains,
            caller=caller,
            reason=f"AVANI review unavailable; local degraded Worldview guard applied: {exc}",
            snapshot_meta=snapshot_meta,
        )
