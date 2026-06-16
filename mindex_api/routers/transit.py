"""
Transit GTFS-RT router — CREP Earth Simulator ground movers (Jun 15, 2026).

BBox-scoped GeoJSON for live vehicles and static route shapes.
Consumed by website BFF: GET /api/crep/transit/vehicles|shapes
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transit", tags=["Transit GTFS-RT"])


def _parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in bbox.split(",")]
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="bbox must be west,south,east,north")
    try:
        west, south, east, north = (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bbox values must be numbers") from exc
    if west >= east or south >= north:
        raise HTTPException(status_code=400, detail="invalid bbox: west<east and south<north required")
    return west, south, east, north


def _geojson_point(lng: float, lat: float) -> Dict[str, Any]:
    return {"type": "Point", "coordinates": [lng, lat]}


def _row_geom_coords(row_geom: Any) -> Optional[List[float]]:
    if row_geom is None:
        return None
    try:
        if isinstance(row_geom, str):
            data = json.loads(row_geom)
        elif isinstance(row_geom, dict):
            data = row_geom
        else:
            data = json.loads(str(row_geom))
        coords = data.get("coordinates")
        if isinstance(coords, list) and len(coords) >= 2:
            return [float(coords[0]), float(coords[1])]
    except Exception:
        return None
    return None


@router.get("/vehicles")
async def transit_vehicles_bbox(
    bbox: str = Query(..., description="west,south,east,north"),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Live transit vehicles as GeoJSON FeatureCollection (CREP contract)."""
    west, south, east, north = _parse_bbox(bbox)

    sql = text(
        """
        SELECT
            v.vehicle_uid,
            v.agency,
            v.route_id,
            v.trip_id,
            v.bearing,
            v.speed,
            v.current_status,
            v.stop_id,
            v.next_stop_eta,
            v.occupancy,
            v.route_short_name,
            v.route_color,
            v.route_type,
            ST_X(v.geom) AS lng,
            ST_Y(v.geom) AS lat,
            v.updated_at
        FROM transit.vehicles v
        WHERE v.geom && ST_MakeEnvelope(:west, :south, :east, :north, 4326)
          AND v.updated_at > now() - interval '10 minutes'
        ORDER BY v.updated_at DESC
        LIMIT 5000
        """
    )

    try:
        result = await session.execute(
            sql,
            {"west": west, "south": south, "east": east, "north": north},
        )
        rows = result.fetchall()
    except Exception as exc:
        if "does not exist" in str(exc):
            return {"type": "FeatureCollection", "features": [], "stale": True, "error": "transit schema not migrated"}
        logger.error("transit vehicles query failed: %s", exc)
        raise HTTPException(status_code=500, detail="transit vehicles query failed") from exc

    features: List[Dict[str, Any]] = []
    for row in rows:
        m = row._mapping
        lng, lat = float(m["lng"]), float(m["lat"])
        route_color = m["route_color"]
        if route_color and not str(route_color).startswith("#"):
            route_color = f"#{route_color}"
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": m["vehicle_uid"],
                    "agency": m["agency"],
                    "route_short_name": m["route_short_name"],
                    "route_color": route_color,
                    "route_type": m["route_type"],
                    "bearing": m["bearing"],
                    "speed": m["speed"],
                    "current_status": m["current_status"],
                    "stop_id": m["stop_id"],
                    "next_stop_eta": m["next_stop_eta"],
                    "occupancy": m["occupancy"],
                    "route_id": m["route_id"],
                    "trip_id": m["trip_id"],
                    "updated_at": m["updated_at"].isoformat() if m["updated_at"] else None,
                },
                "geometry": _geojson_point(lng, lat),
            }
        )

    return {"type": "FeatureCollection", "features": features, "count": len(features)}


@router.get("/shapes")
async def transit_shapes_bbox(
    bbox: str = Query(..., description="west,south,east,north"),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Route centerline shapes as GeoJSON LineString features (CREP contract)."""
    west, south, east, north = _parse_bbox(bbox)

    sql = text(
        """
        SELECT
            s.agency,
            s.route_id,
            s.shape_id,
            s.route_color,
            ST_AsGeoJSON(s.geom)::json AS geometry
        FROM transit.shapes s
        WHERE s.geom && ST_MakeEnvelope(:west, :south, :east, :north, 4326)
        LIMIT 2000
        """
    )

    try:
        result = await session.execute(
            sql,
            {"west": west, "south": south, "east": east, "north": north},
        )
        rows = result.fetchall()
    except Exception as exc:
        if "does not exist" in str(exc):
            return {"type": "FeatureCollection", "features": [], "stale": True, "error": "transit schema not migrated"}
        logger.error("transit shapes query failed: %s", exc)
        raise HTTPException(status_code=500, detail="transit shapes query failed") from exc

    features: List[Dict[str, Any]] = []
    for row in rows:
        m = row._mapping
        geom = m["geometry"]
        if isinstance(geom, str):
            geom = json.loads(geom)
        route_color = m["route_color"]
        if route_color and not str(route_color).startswith("#"):
            route_color = f"#{route_color}"
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "agency": m["agency"],
                    "route_id": m["route_id"],
                    "shape_id": m["shape_id"],
                    "route_color": route_color,
                },
                "geometry": geom,
            }
        )

    return {"type": "FeatureCollection", "features": features, "count": len(features)}


@router.get("/health")
async def transit_health(session: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Lightweight status for CREP transit layer."""
    try:
        v = await session.execute(
            text("SELECT count(*) AS c, max(updated_at) AS last FROM transit.vehicles")
        )
        row = v.fetchone()
        mapping = row._mapping if row else {}
        shapes = await session.execute(text("SELECT count(*) AS c FROM transit.shapes"))
        shape_row = shapes.fetchone()
        return {
            "status": "ok",
            "vehicles": int(mapping.get("c") or 0),
            "shapes": int(shape_row._mapping.get("c") or 0) if shape_row else 0,
            "last_vehicle_update": (
                mapping.get("last").isoformat() if mapping.get("last") else None
            ),
        }
    except Exception as exc:
        if "does not exist" in str(exc):
            return {"status": "unavailable", "error": "transit schema not migrated"}
        return {"status": "error", "error": str(exc)}
