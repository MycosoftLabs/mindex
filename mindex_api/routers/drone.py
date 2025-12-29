"""MycoDRONE API router for MINDEX."""

from datetime import datetime
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import text
from ..dependencies import get_db, pagination_params
from ..schemas.drone import (
    DroneCreate,
    DroneResponse,
    DroneMissionCreate,
    DroneMissionResponse,
    DroneTelemetryIngest,
    DroneStatusResponse,
    DockCreate,
    DockResponse,
)

router = APIRouter(prefix="/drone", tags=["drone"])


@router.post("/drones", response_model=DroneResponse)
async def create_drone(drone: DroneCreate, db=Depends(get_db)):
    """Create drone registry entry."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO telemetry.drone
                (device_id, drone_type, max_payload_kg, max_range_km,
                 home_latitude, home_longitude, dock_id)
                VALUES (:device_id, :drone_type, :max_payload_kg, :max_range_km,
                        :home_latitude, :home_longitude, :dock_id)
                RETURNING *
            """),
            {
                "device_id": str(drone.device_id),
                "drone_type": drone.drone_type,
                "max_payload_kg": drone.max_payload_kg,
                "max_range_km": drone.max_range_km,
                "home_latitude": drone.home_latitude,
                "home_longitude": drone.home_longitude,
                "dock_id": str(drone.dock_id) if drone.dock_id else None,
            },
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(500, "Failed to create drone")
        return dict(row._mapping)


@router.get("/drones/{drone_id}", response_model=DroneResponse)
async def get_drone(drone_id: UUID, db=Depends(get_db)):
    """Get drone registry entry."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("SELECT * FROM telemetry.drone WHERE id = :drone_id"),
            {"drone_id": str(drone_id)},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(404, "Drone not found")
        return dict(row._mapping)


@router.post("/missions", response_model=DroneMissionResponse)
async def create_mission(mission: DroneMissionCreate, db=Depends(get_db)):
    """Create drone mission."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO telemetry.drone_mission
                (drone_id, mission_type, target_device_id, waypoint_lat,
                 waypoint_lon, waypoint_alt, status, progress)
                VALUES (:drone_id, :mission_type, :target_device_id, :waypoint_lat,
                        :waypoint_lon, :waypoint_alt, 'pending', 0)
                RETURNING *
            """),
            {
                "drone_id": str(mission.drone_id),
                "mission_type": mission.mission_type,
                "target_device_id": str(mission.target_device_id) if mission.target_device_id else None,
                "waypoint_lat": mission.waypoint_lat,
                "waypoint_lon": mission.waypoint_lon,
                "waypoint_alt": mission.waypoint_alt,
            },
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(500, "Failed to create mission")
        return dict(row._mapping)


@router.get("/missions/{mission_id}", response_model=DroneMissionResponse)
async def get_mission(mission_id: UUID, db=Depends(get_db)):
    """Get drone mission."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("SELECT * FROM telemetry.drone_mission WHERE id = :mission_id"),
            {"mission_id": str(mission_id)},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(404, "Mission not found")
        return dict(row._mapping)


@router.post("/telemetry/ingest")
async def ingest_telemetry(data: DroneTelemetryIngest, db=Depends(get_db)):
    """Ingest drone telemetry."""
    async with db.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO telemetry.drone_telemetry_log
                (drone_id, timestamp, latitude, longitude, altitude_msl, altitude_rel,
                 heading, ground_speed, battery_percent, battery_voltage, flight_mode,
                 mission_state, payload_latched, payload_type, temp_c, humidity_rh)
                VALUES (:drone_id, :timestamp, :latitude, :longitude, :altitude_msl,
                        :altitude_rel, :heading, :ground_speed, :battery_percent,
                        :battery_voltage, :flight_mode, :mission_state, :payload_latched,
                        :payload_type, :temp_c, :humidity_rh)
            """),
            {
                "drone_id": str(data.drone_id),
                "timestamp": data.timestamp,
                "latitude": data.latitude,
                "longitude": data.longitude,
                "altitude_msl": data.altitude_msl,
                "altitude_rel": data.altitude_rel,
                "heading": data.heading,
                "ground_speed": data.ground_speed,
                "battery_percent": data.battery_percent,
                "battery_voltage": data.battery_voltage,
                "flight_mode": data.flight_mode,
                "mission_state": data.mission_state,
                "payload_latched": data.payload_latched,
                "payload_type": data.payload_type,
                "temp_c": data.temp_c,
                "humidity_rh": data.humidity_rh,
            },
        )
    return {"status": "ingested", "drone_id": str(data.drone_id)}


@router.get("/status", response_model=List[DroneStatusResponse])
async def get_drone_status(db=Depends(get_db)):
    """Get drone status for all drones."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("SELECT * FROM app.v_drone_status")
        )
        return [dict(row._mapping) for row in result]


@router.get("/drones/{drone_id}/telemetry/latest", response_model=DroneTelemetryIngest)
async def get_latest_telemetry(drone_id: UUID, db=Depends(get_db)):
    """Get latest telemetry for a drone."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
                SELECT * FROM telemetry.drone_telemetry_log
                WHERE drone_id = :drone_id
                ORDER BY timestamp DESC
                LIMIT 1
            """),
            {"drone_id": str(drone_id)},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(404, "No telemetry found")
        return dict(row._mapping)


@router.post("/docks", response_model=DockResponse)
async def create_dock(dock: DockCreate, db=Depends(get_db)):
    """Create docking station."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO telemetry.dock
                (name, latitude, longitude, altitude, fiducial_id, charging_bays)
                VALUES (:name, :latitude, :longitude, :altitude, :fiducial_id, :charging_bays)
                RETURNING *
            """),
            {
                "name": dock.name,
                "latitude": dock.latitude,
                "longitude": dock.longitude,
                "altitude": dock.altitude,
                "fiducial_id": dock.fiducial_id,
                "charging_bays": dock.charging_bays,
            },
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(500, "Failed to create dock")
        return dict(row._mapping)


@router.get("/docks", response_model=List[DockResponse])
async def get_docks(db=Depends(get_db)):
    """Get all docking stations."""
    async with db.begin() as conn:
        result = await conn.execute(
            text("SELECT * FROM telemetry.dock ORDER BY name")
        )
        return [dict(row._mapping) for row in result]

