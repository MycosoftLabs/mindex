from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx

from mindex_etl.sources.noaa import fetch_co2_trends, fetch_ch4_trends
from mindex_etl.sources.ais_marine import fetch_aishub_vessels, map_ais_vessel

router = APIRouter(prefix="/emissions", tags=["emissions"])

class EmissionTrendResponse(BaseModel):
    gas_type: str
    year: int
    month: int
    value: float
    unit: str

class VesselResponse(BaseModel):
    source: str
    mmsi: str
    name: Optional[str]
    vessel_type: str
    lat: Optional[float]
    lng: Optional[float]
    speed_knots: Optional[float]
    estimated_emissions_kg_hr: Optional[float]
    
@router.get("/co2-trend", response_model=List[EmissionTrendResponse])
async def get_co2_trend():
    """Fetch global CO2 trend data from NOAA GML."""
    try:
        with httpx.Client() as client:
            data = fetch_co2_trends(client)
            return [
                EmissionTrendResponse(
                    gas_type="co2",
                    year=int(rec.get("year", 0)),
                    month=int(rec.get("month", 0)),
                    value=float(rec.get("value", 0)),
                    unit="ppm",
                ) for rec in data[-24:] # Last 2 years
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/methane-trend", response_model=List[EmissionTrendResponse])
async def get_methane_trend():
    """Fetch global Methane (CH4) trend data from NOAA GML."""
    try:
        with httpx.Client() as client:
            data = fetch_ch4_trends(client)
            return [
                EmissionTrendResponse(
                    gas_type="ch4",
                    year=int(rec.get("year", 0)),
                    month=int(rec.get("month", 0)),
                    value=float(rec.get("value", 0)),
                    unit="ppb",
                ) for rec in data[-24:] # Last 2 years
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/vessels", response_model=List[VesselResponse])
async def get_active_vessels():
    """Fetch active vessels from AIS and correlate with estimated emissions."""
    try:
        with httpx.Client() as client:
            raw_vessels = fetch_aishub_vessels(client)
            if not raw_vessels:
                # Fallback to simulated data if no API key is set
                raw_vessels = [
                    {"MMSI": "123456789", "NAME": "ARCTIC VOYAGER", "TYPE": 70, "LATITUDE": 40.7128, "LONGITUDE": -74.0060, "SOG": 15.2},
                    {"MMSI": "987654321", "NAME": "PACIFIC TITAN", "TYPE": 80, "LATITUDE": 34.0522, "LONGITUDE": -118.2437, "SOG": 12.5},
                    {"MMSI": "456123789", "NAME": "MSC ISABELLA", "TYPE": 74, "LATITUDE": 52.3676, "LONGITUDE": 4.9041, "SOG": 18.1},
                    {"MMSI": "111222333", "NAME": "OASIS OF THE SEAS", "TYPE": 60, "LATITUDE": 25.7617, "LONGITUDE": -80.1918, "SOG": 20.5},
                ]
            
            results = []
            for v in raw_vessels:
                mapped = map_ais_vessel(v)
                speed = mapped.get("speed_knots") or 10.0
                type_name = mapped.get("vessel_type", "")
                
                # Estimate emissions based on maritime type and speed
                base_emission_rate = 100.0 # kg/hr
                if "cargo" in type_name or "tanker" in type_name:
                    base_emission_rate = 500.0
                elif "passenger" in type_name:
                    base_emission_rate = 300.0
                    
                estimated_emissions = base_emission_rate * (speed / 10.0)
                
                results.append(VesselResponse(
                    source=mapped["source"],
                    mmsi=mapped["mmsi"],
                    name=mapped["name"],
                    vessel_type=mapped["vessel_type"],
                    lat=mapped["lat"],
                    lng=mapped["lng"],
                    speed_knots=mapped.get("speed_knots"),
                    estimated_emissions_kg_hr=round(estimated_emissions, 2)
                ))
            return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
