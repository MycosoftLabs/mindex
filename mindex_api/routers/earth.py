"""
Earth Data Router — CREP Map Pipeline & Domain-Specific Queries

Provides:
1. CREP map entity endpoints (spatial queries for map rendering)
2. Domain-specific detail endpoints (earthquakes, aircraft, vessels, etc.)
3. Real-time feed proxies for live data streams
4. Data ingest endpoints for local storage pipeline

All results carry lat/lng + entity_type for direct CREP map overlay rendering.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/earth", tags=["Earth Data"])


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class MapEntity(BaseModel):
    """CREP map-renderable entity — every entity on the planet."""
    id: str
    entity_type: str
    domain: str
    name: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    occurred_at: Optional[str] = None
    source: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)


class MapBounds(BaseModel):
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float


class MapLayerResponse(BaseModel):
    layer: str
    entities: List[MapEntity]
    total: int
    bounds: Optional[MapBounds] = None


class EarthStatsResponse(BaseModel):
    domains: Dict[str, int]
    total_entities: int
    last_updated: Optional[str] = None


# =============================================================================
# HELPER
# =============================================================================

async def _safe_query(session: AsyncSession, sql: str, params: dict, label: str) -> list:
    try:
        result = await session.execute(text(sql), params)
        return result.fetchall()
    except Exception as e:
        if "does not exist" not in str(e):
            logger.error(f"Earth {label} query error: {e}")
        return []


async def _count_table(session: AsyncSession, table: str) -> int:
    try:
        result = await session.execute(text(f"SELECT count(*) FROM {table}"))
        return result.scalar_one()
    except Exception:
        return 0


# =============================================================================
# STATS
# =============================================================================

@router.get("/stats", response_model=EarthStatsResponse)
async def earth_stats(session: AsyncSession = Depends(get_db_session)):
    """Get entity counts across all Earth data domains."""
    tables = {
        "earthquakes": "earth.earthquakes",
        "volcanoes": "earth.volcanoes",
        "wildfires": "earth.wildfires",
        "storms": "earth.storms",
        "lightning": "earth.lightning",
        "tornadoes": "earth.tornadoes",
        "floods": "earth.floods",
        "species": "species.organisms",
        "sightings": "species.sightings",
        "facilities": "infra.facilities",
        "power_grid": "infra.power_grid",
        "water_systems": "infra.water_systems",
        "internet_cables": "infra.internet_cables",
        "antennas": "signals.antennas",
        "wifi_hotspots": "signals.wifi_hotspots",
        "aircraft": "transport.aircraft",
        "vessels": "transport.vessels",
        "airports": "transport.airports",
        "ports": "transport.ports",
        "spaceports": "transport.spaceports",
        "launches": "transport.launches",
        "satellites": "space.satellites",
        "solar_events": "space.solar_events",
        "air_quality": "atmos.air_quality",
        "greenhouse_gas": "atmos.greenhouse_gas",
        "weather": "atmos.weather_observations",
        "remote_sensing": "atmos.remote_sensing",
        "buoys": "hydro.buoys",
        "stream_gauges": "hydro.stream_gauges",
        "cameras": "monitor.cameras",
        "military": "military.installations",
        "taxa": "core.taxon",
        "crep_entities": "crep.unified_entities",
    }

    counts = {}
    total = 0
    for key, table in tables.items():
        c = await _count_table(session, table)
        counts[key] = c
        total += c

    return EarthStatsResponse(domains=counts, total_entities=total)


# =============================================================================
# MAP LAYERS — Spatial queries for CREP map rendering
# =============================================================================

@router.get("/map/bbox", response_model=MapLayerResponse)
async def map_bbox_query(
    layer: str = Query(..., description="Entity type layer to query"),
    lat_min: float = Query(...),
    lat_max: float = Query(...),
    lng_min: float = Query(...),
    lng_max: float = Query(...),
    limit: int = Query(500, ge=1, le=5000),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Get entities within a bounding box for CREP map rendering.

    Layers: earthquakes, volcanoes, wildfires, storms, species, facilities,
    antennas, aircraft, vessels, airports, ports, satellites, cameras,
    military, buoys, weather, air_quality, wifi_hotspots
    """
    layer_queries = {
        "earthquakes": """
            SELECT id::text, 'earthquake' as entity_type, 'earth_events' as domain,
                   'M' || magnitude || ' ' || COALESCE(place_name, '') as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   occurred_at::text, source, jsonb_build_object(
                       'magnitude', magnitude, 'depth_km', depth_km, 'alert_level', alert_level
                   ) as properties
            FROM earth.earthquakes
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            ORDER BY occurred_at DESC LIMIT :limit
        """,
        "volcanoes": """
            SELECT id::text, 'volcano' as entity_type, 'earth_events' as domain,
                   name, ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   created_at::text as occurred_at, source,
                   jsonb_build_object('type', volcano_type, 'elevation_m', elevation_m, 'alert_level', alert_level) as properties
            FROM earth.volcanoes
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            LIMIT :limit
        """,
        "wildfires": """
            SELECT id::text, 'wildfire' as entity_type, 'earth_events' as domain,
                   COALESCE(name, 'Fire') as name,
                   ST_Y(ST_Centroid(location::geometry)) as lat, ST_X(ST_Centroid(location::geometry)) as lng,
                   detected_at::text as occurred_at, source,
                   jsonb_build_object('status', status, 'acres', area_acres, 'frp', frp) as properties
            FROM earth.wildfires
            WHERE ST_Centroid(location::geometry) && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)
            ORDER BY detected_at DESC LIMIT :limit
        """,
        "facilities": """
            SELECT id::text, facility_type as entity_type, 'infrastructure' as domain,
                   name, ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   created_at::text as occurred_at, source,
                   jsonb_build_object('type', facility_type, 'sub_type', sub_type, 'operator', operator) as properties
            FROM infra.facilities
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            LIMIT :limit
        """,
        "antennas": """
            SELECT id::text, antenna_type as entity_type, 'signals' as domain,
                   antenna_type || ' — ' || COALESCE(operator, 'Unknown') as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   created_at::text as occurred_at, source,
                   jsonb_build_object('technology', technology, 'frequency_mhz', frequency_mhz) as properties
            FROM signals.antennas
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            LIMIT :limit
        """,
        "aircraft": """
            SELECT id::text, 'aircraft' as entity_type, 'transport' as domain,
                   COALESCE(callsign, registration, icao24) as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   observed_at::text as occurred_at, source,
                   jsonb_build_object('altitude_ft', altitude_ft, 'speed_kts', ground_speed_kts, 'heading', heading) as properties
            FROM transport.aircraft
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            ORDER BY observed_at DESC LIMIT :limit
        """,
        "vessels": """
            SELECT id::text, 'vessel' as entity_type, 'transport' as domain,
                   COALESCE(name, 'MMSI:' || mmsi) as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   observed_at::text as occurred_at, source,
                   jsonb_build_object('type', vessel_type, 'speed_kts', speed_knots, 'destination', destination) as properties
            FROM transport.vessels
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            ORDER BY observed_at DESC LIMIT :limit
        """,
        "airports": """
            SELECT id::text, 'airport' as entity_type, 'transport' as domain,
                   name || ' (' || COALESCE(iata_code, icao_code) || ')' as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   created_at::text as occurred_at, source,
                   jsonb_build_object('icao', icao_code, 'iata', iata_code, 'type', airport_type) as properties
            FROM transport.airports
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            LIMIT :limit
        """,
        "ports": """
            SELECT id::text, 'port' as entity_type, 'transport' as domain,
                   name, ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   created_at::text as occurred_at, source,
                   jsonb_build_object('type', port_type, 'unlocode', unlocode, 'country', country) as properties
            FROM transport.ports
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            LIMIT :limit
        """,
        "cameras": """
            SELECT id::text, COALESCE(camera_type, 'camera') as entity_type, 'monitoring' as domain,
                   COALESCE(name, 'Camera') as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   last_checked::text as occurred_at, source,
                   jsonb_build_object('type', camera_type, 'status', status, 'stream_url', stream_url) as properties
            FROM monitor.cameras
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            LIMIT :limit
        """,
        "military": """
            SELECT id::text, COALESCE(installation_type, 'installation') as entity_type, 'military' as domain,
                   name, ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   created_at::text as occurred_at, source,
                   jsonb_build_object('branch', branch, 'type', installation_type, 'country', country) as properties
            FROM military.installations
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            LIMIT :limit
        """,
        "buoys": """
            SELECT id::text, COALESCE(buoy_type, 'buoy') as entity_type, 'water' as domain,
                   COALESCE(name, 'Buoy ' || station_id) as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   observed_at::text as occurred_at, source,
                   jsonb_build_object('water_temp_c', water_temp_c, 'wave_height_m', wave_height_m) as properties
            FROM hydro.buoys
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            ORDER BY observed_at DESC LIMIT :limit
        """,
        "weather": """
            SELECT id::text, 'weather_station' as entity_type, 'atmosphere' as domain,
                   COALESCE(station_name, station_id) as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   observed_at::text as occurred_at, source,
                   jsonb_build_object('temp_c', temperature_c, 'humidity', humidity_pct, 'wind_ms', wind_speed_ms) as properties
            FROM atmos.weather_observations
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            ORDER BY observed_at DESC LIMIT :limit
        """,
        "air_quality": """
            SELECT id::text, 'air_quality' as entity_type, 'atmosphere' as domain,
                   COALESCE(station_name, 'AQ Station') || ' — ' || parameter as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   measured_at::text as occurred_at, source,
                   jsonb_build_object('parameter', parameter, 'value', value, 'unit', unit) as properties
            FROM atmos.air_quality
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            ORDER BY measured_at DESC LIMIT :limit
        """,
        "wifi_hotspots": """
            SELECT id::text, 'wifi_hotspot' as entity_type, 'signals' as domain,
                   COALESCE(ssid, '[Hidden] ' || bssid) as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   last_seen::text as occurred_at, source,
                   jsonb_build_object('encryption', encryption, 'channel', channel) as properties
            FROM signals.wifi_hotspots
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            LIMIT :limit
        """,
        "species": """
            SELECT s.id::text, o.kingdom as entity_type, 'species' as domain,
                   o.scientific_name || COALESCE(' (' || o.common_name || ')', '') as name,
                   ST_Y(s.location::geometry) as lat, ST_X(s.location::geometry) as lng,
                   s.observed_at::text, s.source,
                   jsonb_build_object('kingdom', o.kingdom, 'conservation', o.conservation_status) as properties
            FROM species.sightings s
            JOIN species.organisms o ON o.id = s.organism_id
            WHERE s.location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            ORDER BY s.observed_at DESC LIMIT :limit
        """,
        "power_grid": """
            SELECT id::text, asset_type as entity_type, 'infrastructure' as domain,
                   COALESCE(name, asset_type || ' ' || voltage_kv || 'kV') as name,
                   ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   created_at::text as occurred_at, source,
                   jsonb_build_object('asset_type', asset_type, 'voltage_kv', voltage_kv, 'operator', operator) as properties
            FROM infra.power_grid
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            LIMIT :limit
        """,
        "internet_cables": """
            SELECT id::text, 'submarine_cable' as entity_type, 'infrastructure' as domain,
                   name,
                   ST_Y(ST_Centroid(route::geometry)) as lat,
                   ST_X(ST_Centroid(route::geometry)) as lng,
                   created_at::text as occurred_at, source,
                   jsonb_build_object(
                       'cable_type', cable_type, 'status', status,
                       'length_km', length_km, 'capacity_tbps', capacity_tbps,
                       'route', ST_AsGeoJSON(route::geometry)::jsonb
                   ) as properties
            FROM infra.internet_cables
            WHERE route IS NOT NULL
            LIMIT :limit
        """,
        "satellites": """
            SELECT id::text, 'satellite' as entity_type, 'space' as domain,
                   name, ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng,
                   observed_at::text as occurred_at, source,
                   jsonb_build_object('norad_id', norad_id, 'orbit_type', orbit_type, 'altitude_km', altitude_km) as properties
            FROM space.satellites
            WHERE location && ST_MakeEnvelope(:lng_min, :lat_min, :lng_max, :lat_max, 4326)::geography
            ORDER BY observed_at DESC LIMIT :limit
        """,
    }

    sql = layer_queries.get(layer)
    if not sql:
        return MapLayerResponse(
            layer=layer, entities=[], total=0,
            bounds=MapBounds(lat_min=lat_min, lat_max=lat_max, lng_min=lng_min, lng_max=lng_max),
        )

    params = {"lat_min": lat_min, "lat_max": lat_max, "lng_min": lng_min, "lng_max": lng_max, "limit": limit}
    rows = await _safe_query(session, sql, params, f"map_{layer}")

    entities = []
    for r in rows:
        entities.append(MapEntity(
            id=r.id, entity_type=r.entity_type, domain=r.domain,
            name=r.name, lat=r.lat, lng=r.lng,
            occurred_at=r.occurred_at, source=r.source,
            properties=r.properties if isinstance(r.properties, dict) else {},
        ))

    return MapLayerResponse(
        layer=layer,
        entities=entities,
        total=len(entities),
        bounds=MapBounds(lat_min=lat_min, lat_max=lat_max, lng_min=lng_min, lng_max=lng_max),
    )


@router.get("/map/layers")
async def available_layers():
    """List all available CREP map layers."""
    return {
        "layers": [
            {"name": "earthquakes", "domain": "earth_events", "description": "Seismic events (USGS)"},
            {"name": "volcanoes", "domain": "earth_events", "description": "Active and dormant volcanoes"},
            {"name": "wildfires", "domain": "earth_events", "description": "Active fires (FIRMS/NIFC)"},
            {"name": "facilities", "domain": "infrastructure", "description": "Factories, power plants, mining, dams"},
            {"name": "antennas", "domain": "signals", "description": "Cell towers, AM/FM, broadcast antennas"},
            {"name": "wifi_hotspots", "domain": "signals", "description": "Known WiFi/Bluetooth networks"},
            {"name": "aircraft", "domain": "transport", "description": "Live aircraft positions (ADS-B)"},
            {"name": "vessels", "domain": "transport", "description": "Ship positions (AIS)"},
            {"name": "airports", "domain": "transport", "description": "Airports and airfields"},
            {"name": "ports", "domain": "transport", "description": "Seaports and harbors"},
            {"name": "cameras", "domain": "monitoring", "description": "Public webcams and CCTV"},
            {"name": "military", "domain": "military", "description": "Known military installations"},
            {"name": "buoys", "domain": "water", "description": "Ocean buoys and sensors"},
            {"name": "weather", "domain": "atmosphere", "description": "Weather observation stations"},
            {"name": "air_quality", "domain": "atmosphere", "description": "Air quality monitoring stations"},
            {"name": "species", "domain": "biological", "description": "Species observations/sightings"},
        ],
    }


# =============================================================================
# CREP ENTITY SYNC — Push search results into crep.unified_entities
# =============================================================================

@router.post("/crep/sync")
async def sync_to_crep(
    entity_type: str = Query(..., description="Entity type to sync to CREP"),
    limit: int = Query(1000, ge=1, le=10000),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Sync entities from domain tables into crep.unified_entities for map rendering.

    This creates/updates the CREP unified entity layer from source tables,
    enabling the CREP map to render all data types in a single layer.
    """
    sync_queries = {
        "earthquakes": """
            INSERT INTO crep.unified_entities (id, entity_type, geometry, state, observed_at, valid_from, source, confidence, s2_cell_id)
            SELECT
                'eq_' || id::text, 'earthquake', location,
                jsonb_build_object('magnitude', magnitude, 'place', place_name, 'depth_km', depth_km, 'alert', alert_level),
                occurred_at, occurred_at, source,
                CASE WHEN magnitude >= 6 THEN 1.0 WHEN magnitude >= 4 THEN 0.8 ELSE 0.6 END,
                0
            FROM earth.earthquakes
            ORDER BY occurred_at DESC LIMIT :limit
            ON CONFLICT (id, observed_at) DO UPDATE SET state = EXCLUDED.state
        """,
        "wildfires": """
            INSERT INTO crep.unified_entities (id, entity_type, geometry, state, observed_at, valid_from, source, confidence, s2_cell_id)
            SELECT
                'fire_' || id::text, 'wildfire', location,
                jsonb_build_object('name', name, 'status', status, 'acres', area_acres, 'frp', frp),
                detected_at, detected_at, source, COALESCE(confidence::float / 100, 0.7),
                0
            FROM earth.wildfires
            ORDER BY detected_at DESC LIMIT :limit
            ON CONFLICT (id, observed_at) DO UPDATE SET state = EXCLUDED.state
        """,
        "aircraft": """
            INSERT INTO crep.unified_entities (id, entity_type, geometry, state, observed_at, valid_from, source, confidence, s2_cell_id)
            SELECT
                'ac_' || id::text, 'aircraft', location,
                jsonb_build_object('callsign', callsign, 'icao24', icao24, 'altitude_ft', altitude_ft, 'speed_kts', ground_speed_kts),
                observed_at, observed_at, source, 0.95,
                0
            FROM transport.aircraft WHERE location IS NOT NULL
            ORDER BY observed_at DESC LIMIT :limit
            ON CONFLICT (id, observed_at) DO UPDATE SET state = EXCLUDED.state
        """,
        "vessels": """
            INSERT INTO crep.unified_entities (id, entity_type, geometry, state, observed_at, valid_from, source, confidence, s2_cell_id)
            SELECT
                'v_' || id::text, 'vessel', location,
                jsonb_build_object('name', name, 'mmsi', mmsi, 'type', vessel_type, 'speed_kts', speed_knots, 'destination', destination),
                observed_at, observed_at, source, 0.9,
                0
            FROM transport.vessels WHERE location IS NOT NULL
            ORDER BY observed_at DESC LIMIT :limit
            ON CONFLICT (id, observed_at) DO UPDATE SET state = EXCLUDED.state
        """,
    }

    sql = sync_queries.get(entity_type)
    if not sql:
        return {"error": f"No sync query for entity_type: {entity_type}", "available": list(sync_queries.keys())}

    try:
        result = await session.execute(text(sql), {"limit": limit})
        await session.commit()
        return {"synced": entity_type, "status": "ok", "rows_affected": result.rowcount}
    except Exception as e:
        return {"error": str(e), "entity_type": entity_type}


# =============================================================================
# DOMAIN-SPECIFIC DETAIL ENDPOINTS
# =============================================================================

@router.get("/earthquakes/recent")
async def recent_earthquakes(
    hours: int = Query(24, ge=1, le=720),
    min_magnitude: float = Query(2.5),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),
):
    """Get recent earthquakes within the last N hours."""
    sql = """
        SELECT id::text, source, magnitude, magnitude_type, depth_km, place_name,
               occurred_at::text, alert_level, tsunami_flag, felt_reports,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM earth.earthquakes
        WHERE occurred_at >= NOW() - (:hours || ' hours')::interval
          AND magnitude >= :min_mag
        ORDER BY occurred_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"hours": hours, "min_mag": min_magnitude, "limit": limit}, "recent_eq")
    return {"earthquakes": [dict(r._mapping) for r in rows], "total": len(rows)}


@router.get("/satellites/active")
async def active_satellites(
    satellite_type: Optional[str] = Query(None, description="Filter by type: weather, gps, comm, earth_obs"),
    orbit_type: Optional[str] = Query(None, description="Filter by orbit: LEO, MEO, GEO, SSO"),
    limit: int = Query(100, ge=1, le=5000),
    session: AsyncSession = Depends(get_db_session),
):
    """Get active satellites with optional type/orbit filter."""
    where = "status = 'active'"
    params: Dict[str, Any] = {"limit": limit}
    if satellite_type:
        where += " AND satellite_type = :sat_type"
        params["sat_type"] = satellite_type
    if orbit_type:
        where += " AND orbit_type = :orbit_type"
        params["orbit_type"] = orbit_type

    sql = f"""
        SELECT id::text, name, satellite_type, operator, orbit_type,
               perigee_km, apogee_km, inclination_deg, norad_id, cospar_id
        FROM space.satellites WHERE {where}
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "satellites")
    return {"satellites": [dict(r._mapping) for r in rows], "total": len(rows)}


@router.get("/solar/recent")
async def recent_solar_events(
    days: int = Query(30, ge=1, le=365),
    event_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
):
    """Get recent solar/space weather events."""
    where = "start_time >= NOW() - (:days || ' days')::interval"
    params: Dict[str, Any] = {"days": days, "limit": limit}
    if event_type:
        where += " AND event_type = :etype"
        params["etype"] = event_type

    sql = f"""
        SELECT id::text, source, event_type, class, intensity, kp_index,
               speed_km_s, source_region, start_time::text, peak_time::text, earth_directed
        FROM space.solar_events WHERE {where}
        ORDER BY start_time DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "solar")
    return {"solar_events": [dict(r._mapping) for r in rows], "total": len(rows)}


# =============================================================================
# INFRASTRUCTURE STATUS — Storage, Cache, Sync Health
# =============================================================================

@router.get("/infrastructure")
async def infrastructure_status(session: AsyncSession = Depends(get_db_session)):
    """
    Get MINDEX infrastructure status: storage tiers, cache health, sync state.

    Shows:
    - PostgreSQL (hot tier) status and entity counts
    - Redis cache connectivity and hit rates
    - Supabase (warm tier) connectivity
    - NAS (cold tier) mount status and disk usage
    - Data pipeline health
    """
    from ..cache import get_cache
    from ..supabase_client import get_supabase
    from ..storage import get_storage

    cache = get_cache()
    supa = get_supabase()
    storage = get_storage()

    status = {
        "tiers": {
            "hot": {
                "type": "PostgreSQL + PostGIS",
                "status": "online",
                "latency": "<5ms",
            },
            "cache": {
                "type": "Redis" if cache.connected else "In-process LRU",
                "status": "online" if cache.connected else "fallback",
                "latency": "<1ms" if cache.connected else "<0.1ms (LRU only)",
            },
            "warm": {
                "type": "Supabase",
                "status": "online" if supa.enabled else "not_configured",
                "latency": "<50ms global",
                "url": supa.url if supa.enabled else None,
            },
            "cold": {
                "type": "NAS (Ubiquiti)",
                **storage.nas_usage(),
            },
        },
        "pipeline": {
            "strategy": "local-first with live-scrape fallback",
            "read_order": ["LRU cache", "Redis", "PostgreSQL", "Supabase", "Live scrape"],
            "write_order": ["PostgreSQL (sync)", "Redis cache (sync)", "Supabase (async)", "NAS archive (async)"],
            "scrape_on_miss": True,
            "auto_cache": True,
        },
    }

    return status


# =============================================================================
# INGEST — Push earth data into MINDEX from ETL pipelines or CREP collectors
# =============================================================================

class IngestEntity(BaseModel):
    """Single entity to ingest into an earth domain table."""
    source: str
    source_id: Optional[str] = None
    name: str
    entity_type: str
    lat: float
    lng: float
    occurred_at: Optional[str] = None
    properties: Dict[str, Any] = {}


class IngestRequest(BaseModel):
    layer: str
    entities: List[IngestEntity]


class IngestResponse(BaseModel):
    layer: str
    inserted: int
    errors: int


@router.post("/ingest", response_model=IngestResponse)
async def ingest_earth_data(
    request: IngestRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Bulk ingest entities into earth domain tables.

    Supported layers: earthquakes, volcanoes, wildfires, facilities,
    power_grid, internet_cables, antennas, aircraft, vessels, airports,
    ports, satellites, solar_events, cameras, military, buoys
    """
    layer = request.layer
    inserted = 0
    errors = 0

    # Map layer to insert SQL
    insert_queries = {
        "earthquakes": """
            INSERT INTO earth.earthquakes (source, source_id, magnitude, depth_km,
                location, place_name, occurred_at, properties)
            VALUES (:source, :source_id, :magnitude, :depth_km,
                ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :name, COALESCE(:occurred_at::timestamptz, NOW()), :props::jsonb)
            ON CONFLICT (source_id) DO UPDATE SET
                magnitude = EXCLUDED.magnitude, properties = EXCLUDED.properties
        """,
        "facilities": """
            INSERT INTO infra.facilities (source, source_id, name, facility_type, sub_type,
                location, operator, capacity, status, properties)
            VALUES (:source, :source_id, :name, :entity_type, :sub_type,
                ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :operator, :capacity, :status, :props::jsonb)
            ON CONFLICT (source, source_id) DO UPDATE SET
                name = EXCLUDED.name, properties = EXCLUDED.properties
        """,
        "power_grid": """
            INSERT INTO infra.power_grid (source, source_id, asset_type, name, voltage_kv,
                location, operator, properties)
            VALUES (:source, :source_id, :entity_type, :name, :voltage_kv,
                ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :operator, :props::jsonb)
            ON CONFLICT DO NOTHING
        """,
        "airports": """
            INSERT INTO transport.airports (source, source_id, name, airport_type,
                icao_code, location, country, properties)
            VALUES (:source, :source_id, :name, :entity_type,
                :icao, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :country, :props::jsonb)
            ON CONFLICT DO NOTHING
        """,
        "aircraft": """
            INSERT INTO transport.aircraft (source, source_id, callsign, icao24,
                location, heading, altitude_ft, ground_speed_kts, observed_at, properties)
            VALUES (:source, :source_id, :name, :icao24,
                ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :heading, :altitude, :speed, COALESCE(:occurred_at::timestamptz, NOW()), :props::jsonb)
            ON CONFLICT DO NOTHING
        """,
        "vessels": """
            INSERT INTO transport.vessels (source, source_id, name, mmsi,
                location, speed_knots, heading, observed_at, properties)
            VALUES (:source, :source_id, :name, :mmsi,
                ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :speed, :heading, COALESCE(:occurred_at::timestamptz, NOW()), :props::jsonb)
            ON CONFLICT DO NOTHING
        """,
        "satellites": """
            INSERT INTO space.satellites (source, source_id, name, norad_id,
                orbit_type, location, altitude_km, observed_at, properties)
            VALUES (:source, :source_id, :name, :norad_id,
                :orbit_type, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :altitude_km, COALESCE(:occurred_at::timestamptz, NOW()), :props::jsonb)
            ON CONFLICT DO NOTHING
        """,
        "antennas": """
            INSERT INTO signals.antennas (source, source_id, antenna_type,
                location, operator, technology, properties)
            VALUES (:source, :source_id, :entity_type,
                ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :operator, :technology, :props::jsonb)
            ON CONFLICT DO NOTHING
        """,
        "military": """
            INSERT INTO military.installations (source, source_id, name, installation_type,
                location, branch, country, properties)
            VALUES (:source, :source_id, :name, :entity_type,
                ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                :branch, :country, :props::jsonb)
            ON CONFLICT DO NOTHING
        """,
    }

    sql_template = insert_queries.get(layer)
    if not sql_template:
        return IngestResponse(layer=layer, inserted=0, errors=len(request.entities))

    for entity in request.entities:
        props = entity.properties
        params = {
            "source": entity.source,
            "source_id": entity.source_id,
            "name": entity.name,
            "entity_type": entity.entity_type,
            "lat": entity.lat,
            "lng": entity.lng,
            "occurred_at": entity.occurred_at,
            "props": json.dumps(props),
            # Layer-specific fields from properties
            "magnitude": props.get("magnitude", 0),
            "depth_km": props.get("depth_km"),
            "sub_type": props.get("sub_type"),
            "operator": props.get("operator"),
            "capacity": props.get("capacity"),
            "status": props.get("status", "active"),
            "voltage_kv": props.get("voltage_kv", 0),
            "icao": props.get("icao"),
            "country": props.get("country"),
            "icao24": props.get("icao24"),
            "heading": props.get("heading"),
            "altitude": props.get("altitude"),
            "speed": props.get("speed"),
            "mmsi": props.get("mmsi"),
            "norad_id": props.get("norad_id"),
            "orbit_type": props.get("orbit_type"),
            "altitude_km": props.get("altitude_km"),
            "technology": props.get("technology"),
            "branch": props.get("branch"),
        }
        try:
            await session.execute(text(sql_template), params)
            inserted += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                logger.warning(f"Ingest error for {layer}/{entity.source_id}: {e}")

    if inserted > 0:
        await session.commit()

    logger.info(f"Earth ingest: {layer} — {inserted} inserted, {errors} errors")
    return IngestResponse(layer=layer, inserted=inserted, errors=errors)
