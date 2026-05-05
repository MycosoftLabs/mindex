from fastapi import APIRouter, HTTPException  # type: ignore
import httpx  # type: ignore
from datetime import datetime
from typing import Any

router = APIRouter(tags=["MWave"])

@router.get("/mwave")
async def get_mwave():
    status: dict[str, Any] = {
        "status": "monitoring",
        "last_updated": datetime.now().isoformat(),
        "sensor_count": 0,
        "active_correlations": 0,
        "prediction_confidence": None,
        "earthquakes": {
            "hour": [],
            "count_hour": 0,
            "count_day": 0,
            "max_magnitude_24h": 0,
        },
        "alerts": [],
        "data_source": "live"
    }
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson")
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                
                # Transform just like Nextjs did
                earthquakes = []
                for f in features:
                    props = f.get("properties", {})
                    geom = f.get("geometry", {})
                    coords = geom.get("coordinates", [0, 0, 0])
                    earthquakes.append({
                        "id": f.get("id"),
                        "magnitude": props.get("mag"),
                        "place": props.get("place"),
                        "time": props.get("time"),
                        "updated": props.get("updated"),
                        "longitude": coords[0],
                        "latitude": coords[1],
                        "depth": coords[2],
                        "url": props.get("url")
                    })
                status["earthquakes"]["hour"] = earthquakes
                status["earthquakes"]["count_hour"] = len(earthquakes)
                
            resp_day = await client.get("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson")
            if resp_day.status_code == 200:
                data = resp_day.json()
                features = data.get("features", [])
                status["earthquakes"]["count_day"] = len(features)
                max_mag = 0
                for f in features:
                    mag = f.get("properties", {}).get("mag")
                    if mag and mag > max_mag:
                        max_mag = mag
                status["earthquakes"]["max_magnitude_24h"] = max_mag
                
                if max_mag >= 7:
                    status["status"] = "critical"
                    status["alerts"].append({
                        "type": "earthquake", "severity": "critical", 
                        "message": f"Major earthquake detected: M{max_mag:.1f}", "timestamp": datetime.now().isoformat()
                    })
                elif max_mag >= 5:
                    status["status"] = "warning"
                    status["alerts"].append({
                        "type": "earthquake", "severity": "warning", 
                        "message": f"Significant earthquake: M{max_mag:.1f}", "timestamp": datetime.now().isoformat()
                    })

    except Exception as e:
        status["status"] = "offline"
        status["data_source"] = "unavailable"
    
    return status


@router.get("/mwave/correlations")
async def mwave_correlations():
    """
    Lightweight correlation stats from the live USGS hour feed (inter-event spacing in minutes).
    No device time-series yet — returns empty `device_pairs` until telemetry joins land.
    """
    base = await get_mwave()
    hour = (base.get("earthquakes") or {}).get("hour") or []
    times_ms: list[int] = []
    for ev in hour:
        t = ev.get("time")
        if isinstance(t, (int, float)):
            times_ms.append(int(t))
    times_ms.sort()
    gaps: list[float] = []
    for a, b in zip(times_ms, times_ms[1:]):
        gaps.append(max(0.0, (b - a) / 60000.0))
    return {
        "status": base.get("status"),
        "last_updated": base.get("last_updated"),
        "event_count_hour": len(hour),
        "inter_event_minutes": {"count": len(gaps), "mean": sum(gaps) / len(gaps) if gaps else None},
        "device_pairs": [],
        "data_source": base.get("data_source"),
    }


@router.get("/mwave/summary")
async def mwave_summary():
    """Compact card payload for FUSARIUM / MYCA embeds — USGS-backed only, no synthetic quake data."""
    full = await get_mwave()
    eq = full.get("earthquakes") or {}
    hour = eq.get("hour") or []
    top = sorted(
        [x for x in hour if isinstance(x.get("magnitude"), (int, float))],
        key=lambda x: float(x["magnitude"]),
        reverse=True,
    )[:5]
    return {
        "status": full.get("status"),
        "last_updated": full.get("last_updated"),
        "count_hour": eq.get("count_hour"),
        "count_day": eq.get("count_day"),
        "max_magnitude_24h": eq.get("max_magnitude_24h"),
        "top_events": top,
        "alerts": full.get("alerts") or [],
        "data_source": full.get("data_source"),
    }
