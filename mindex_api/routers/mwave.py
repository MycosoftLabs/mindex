"""Measured seismic catalog and isolated M-Wave research input contracts."""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ..mwave_research import (
    ResearchInputBatch, base_readiness, current_research_readiness,
    evaluate_research_inputs,
)

router = APIRouter(tags=["MWave"])
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_INPUT_BYTES = 1024 * 1024
USGS_BASE = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary"


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def measured_event(feature: dict[str, Any]) -> dict[str, Any]:
    props = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates") or []
    located = len(coords) >= 2 and finite(coords[0]) and finite(coords[1]) and -180 <= coords[0] <= 180 and -90 <= coords[1] <= 90
    return {
        "id": feature.get("id"), "magnitude": props.get("mag") if finite(props.get("mag")) else None,
        "place": props.get("place"), "time": props.get("time") if finite(props.get("time")) else None,
        "updated": props.get("updated") if finite(props.get("updated")) else None,
        "longitude": coords[0] if located else None, "latitude": coords[1] if located else None,
        "depth": coords[2] if len(coords) > 2 and finite(coords[2]) else None,
        "url": props.get("url"), "source": "USGS", "is_prediction": False,
    }


async def fetch_catalog(client: httpx.AsyncClient, period: str) -> dict[str, Any] | None:
    try:
        async with client.stream("GET", f"{USGS_BASE}/all_{period}.geojson") as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    return None
                chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
            features = payload.get("features")
            if not isinstance(features, list):
                return None
            events = [measured_event(item) for item in features if isinstance(item, dict)]
            generated = (payload.get("metadata") or {}).get("generated")
            generated_at = datetime.fromtimestamp(generated / 1000, timezone.utc).isoformat() if finite(generated) else None
            return {"events": events, "generated_at": generated_at}
    except (httpx.HTTPError, ValueError, TypeError, OverflowError):
        return None


@router.get("/mwave")
async def get_mwave():
    """Actual USGS detections are independent of an unqualified bioelectric research model."""
    feeds: list[Any] = [None, None]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0)) as client:
            feeds = await asyncio.wait_for(asyncio.gather(
                fetch_catalog(client, "hour"), fetch_catalog(client, "day"),
                return_exceptions=True,
            ), timeout=5.0)
    except (TimeoutError, httpx.HTTPError):
        pass
    hour, day = [feed if isinstance(feed, dict) else None for feed in feeds]
    available = sum(feed is not None for feed in (hour, day))
    state = "unavailable" if available == 0 else "partial" if available == 1 else "available"
    hour_events = hour["events"] if hour else []
    day_events = day["events"] if day else []
    magnitudes = [event["magnitude"] for event in day_events if finite(event["magnitude"])]
    maximum = max(magnitudes) if magnitudes else None
    alerts = [
        {"type": "detected-earthquake", "severity": "critical" if event["magnitude"] >= 7 else "warning",
         "message": f"Detected earthquake: M{event['magnitude']:.1f}", "event_id": event["id"],
         "time": event["time"], "url": event["url"], "source": "USGS", "is_prediction": False}
        for event in day_events if finite(event["magnitude"]) and event["magnitude"] >= 5
    ][:32]
    research = base_readiness()
    return {
        "status": "offline" if available == 0 else "partial" if available == 1 else "measurement-only",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "sensor_count": None, "active_correlations": None, "prediction_confidence": None,
        "prediction_available": False, "public_alerts_enabled": False,
        "earthquakes": {"hour": hour_events, "count_hour": len(hour_events) if hour else None,
            "count_day": len(day_events) if day else None, "max_magnitude_24h": maximum},
        "alerts": alerts, "data_source": "USGS" if available else "unavailable",
        "measured_seismic": {"source": "USGS", "state": state, "is_bioelectric_input": False,
            "hour_state": "available" if hour else "unavailable", "day_state": "available" if day else "unavailable",
            "hour_generated_at": hour["generated_at"] if hour else None, "day_generated_at": day["generated_at"] if day else None},
        "research": research,
    }


@router.get("/mwave/readiness")
async def mwave_readiness():
    return await current_research_readiness()


@router.get("/mwave/input-schema")
async def mwave_input_schema():
    return {"contract_version": "mwave.research.v1", "schema": ResearchInputBatch.model_json_schema(),
            "max_request_bytes": MAX_INPUT_BYTES, "persistence_bound": False,
            "prediction_available": False, "public_alerts_enabled": False}


@router.post("/mwave/readiness/evaluate")
async def mwave_evaluate(request: Request):
    """Internal-authenticated, bounded schema assessment; no storage, inference or public alert."""
    chunks: list[bytes] = []
    size = 0
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_INPUT_BYTES:
                    raise HTTPException(status_code=413, detail="Research input exceeds the request budget.")
                chunks.append(chunk)
    except TimeoutError:
        raise HTTPException(status_code=408, detail="Research input exceeded the read deadline.") from None
    try:
        batch = ResearchInputBatch.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        # Do not echo submitted sensor payloads or identifiers into error responses.
        errors = [{"path": ".".join(str(part) for part in error["loc"]), "type": error["type"]} for error in exc.errors()]
        raise HTTPException(status_code=422, detail={"message": "Research input does not match the measured-input schema.", "errors": errors[:32]}) from None
    return evaluate_research_inputs(batch)


@router.get("/mwave/correlations")
async def mwave_correlations():
    """Event spacing is descriptive catalog data, never a measured bioelectric correlation."""
    base = await get_mwave()
    hour = base["earthquakes"]["hour"]
    times = sorted(event["time"] for event in hour if finite(event["time"]))
    gaps = [(b - a) / 60000 for a, b in zip(times, times[1:])]
    return {
        "status": base["status"], "last_updated": base["last_updated"],
        "event_count_hour": base["earthquakes"]["count_hour"],
        "inter_event_minutes": {"count": len(gaps), "mean": sum(gaps) / len(gaps) if gaps else None},
        "device_pairs": [], "bioelectric_correlation_available": False,
        "data_source": base["data_source"], "prediction_available": False,
    }


@router.get("/mwave/summary")
async def mwave_summary():
    full = await get_mwave()
    eq = full["earthquakes"]
    top = sorted([event for event in eq["hour"] if finite(event["magnitude"])],
                 key=lambda event: event["magnitude"], reverse=True)[:5]
    return {
        "status": full["status"], "last_updated": full["last_updated"],
        "count_hour": eq["count_hour"], "count_day": eq["count_day"],
        "max_magnitude_24h": eq["max_magnitude_24h"], "top_events": top,
        "alerts": full["alerts"], "data_source": full["data_source"],
        "measured_seismic": full["measured_seismic"], "prediction_available": False,
        "research": full["research"],
    }
