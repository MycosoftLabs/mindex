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
        "prediction_confidence": 30,
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
