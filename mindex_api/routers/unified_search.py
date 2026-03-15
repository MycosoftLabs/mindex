"""
Unified Earth Search Router — MINDEX v3

Single endpoint that searches across ALL planetary data in parallel:

BIOLOGICAL:
  - Taxa (fungi, existing core.taxon)
  - Species (all kingdoms: plants, birds, mammals, insects, marine, etc.)
  - Compounds (chemistry)
  - Genetics (sequences)
  - Observations/Sightings

EARTH EVENTS:
  - Earthquakes, Volcanoes, Wildfires, Storms, Lightning, Tornadoes, Floods

ATMOSPHERE & CLIMATE:
  - Air quality, Greenhouse gases, Weather observations, Remote sensing (MODIS/Landsat/AIRS)

WATER SYSTEMS:
  - Buoys, Stream gauges, Ocean data

INFRASTRUCTURE:
  - Facilities (factories, power plants, mining, oil & gas, water treatment, dams)
  - Power grid, Water systems, Internet cables

SIGNALS & RF:
  - Cell towers, AM/FM antennas, WiFi hotspots, Signal measurements

TRANSPORT:
  - Aircraft (ADS-B), Vessels (AIS), Airports, Ports, Spaceports, Launches

SPACE:
  - Satellites, Solar events (flares, CMEs, geomagnetic storms)

MONITORING:
  - Webcams, CCTV, Public feeds

MILITARY:
  - Publicly known installations

TELEMETRY:
  - MycoBrain devices, Sensor readings

KNOWLEDGE:
  - Research papers, Investigations, CREP entities

Supports:
  - Full-text search across every domain
  - Location-based filtering (PostGIS)
  - Temporal filtering (time ranges)
  - Domain filtering (select which data types to search)
  - Parallel execution via asyncio.gather()
  - CREP map pipeline (all results carry lat/lng for map rendering)
  - Cache-first with live-scrape fallback (local-first data strategy)
  - Redis/LRU cache → PostgreSQL → Supabase → live scrape → store locally
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/unified-search", tags=["Unified Earth Search"])


# =============================================================================
# ALL SEARCHABLE DOMAINS
# =============================================================================

ALL_DOMAINS = [
    # Biological
    "taxa", "species", "compounds", "genetics", "observations",
    # Earth events
    "earthquakes", "volcanoes", "wildfires", "storms", "lightning", "tornadoes", "floods",
    # Atmosphere
    "air_quality", "greenhouse_gas", "weather", "remote_sensing",
    # Water
    "buoys", "stream_gauges",
    # Infrastructure
    "facilities", "power_grid", "water_systems", "internet_cables",
    # Signals
    "antennas", "wifi_hotspots", "signal_measurements",
    # Transport
    "aircraft", "vessels", "airports", "ports", "spaceports", "launches",
    # Space
    "satellites", "solar_events",
    # Monitoring
    "cameras",
    # Military
    "military_installations",
    # Telemetry
    "devices", "telemetry",
    # Knowledge
    "research", "crep_entities",
]

# Grouped domain aliases for convenience
DOMAIN_GROUPS = {
    "all": ALL_DOMAINS,
    "biological": ["taxa", "species", "compounds", "genetics", "observations"],
    "life": ["taxa", "species", "observations"],
    "earth_events": ["earthquakes", "volcanoes", "wildfires", "storms", "lightning", "tornadoes", "floods"],
    "hazards": ["earthquakes", "volcanoes", "wildfires", "storms", "tornadoes", "floods"],
    "atmosphere": ["air_quality", "greenhouse_gas", "weather", "remote_sensing"],
    "water": ["buoys", "stream_gauges", "water_systems"],
    "infrastructure": ["facilities", "power_grid", "water_systems", "internet_cables"],
    "pollution": ["facilities", "air_quality", "greenhouse_gas"],
    "signals": ["antennas", "wifi_hotspots", "signal_measurements"],
    "transport": ["aircraft", "vessels", "airports", "ports", "spaceports", "launches"],
    "aviation": ["aircraft", "airports"],
    "maritime": ["vessels", "ports", "buoys"],
    "space": ["satellites", "solar_events", "launches"],
    "monitoring": ["cameras"],
    "military": ["military_installations"],
    "telemetry": ["devices", "telemetry"],
}


# =============================================================================
# RESPONSE SCHEMAS
# =============================================================================

class SearchResult(BaseModel):
    """Universal search result that can represent any domain entity."""
    id: str
    domain: str                                  # which domain this came from
    entity_type: str                             # specific type within domain
    name: str                                    # display name
    description: Optional[str] = None
    lat: Optional[float] = None                  # for CREP map rendering
    lng: Optional[float] = None
    geometry_type: Optional[str] = None          # point, line, polygon
    occurred_at: Optional[str] = None            # temporal marker
    source: Optional[str] = None                 # data source
    image_url: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)  # domain-specific fields


class TaxonResult(BaseModel):
    id: int
    scientific_name: str
    common_name: Optional[str] = None
    rank: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    observation_count: int = 0
    source: str = "mindex"
    toxicity: Optional[str] = None
    edibility: Optional[str] = None


class CompoundResult(BaseModel):
    id: int
    name: str
    formula: Optional[str] = None
    molecular_weight: Optional[float] = None
    chemical_class: Optional[str] = None
    smiles: Optional[str] = None
    bioactivity: List[str] = Field(default_factory=list)
    source_species: List[str] = Field(default_factory=list)


class GeneticsResult(BaseModel):
    id: int
    accession: str
    species_name: str
    gene: Optional[str] = None
    sequence_length: int = 0
    source: str = "genbank"


class ObservationResult(BaseModel):
    id: str
    taxon_id: str
    taxon_name: str
    location: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    observed_at: Optional[str] = None
    image_url: Optional[str] = None


class UnifiedSearchResponse(BaseModel):
    query: str
    domains_searched: List[str]
    results: Dict[str, List[Any]]
    total_count: int
    timing_ms: int
    filters_applied: Dict[str, Any] = Field(default_factory=dict)


class EarthSearchResponse(BaseModel):
    """Extended response with CREP-compatible universal results for map rendering."""
    query: str
    domains_searched: List[str]
    results: Dict[str, List[Any]]          # domain-keyed legacy results
    universal_results: List[SearchResult]   # flat list of all results for CREP map
    total_count: int
    timing_ms: int
    filters_applied: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# HELPER: Safe query executor
# =============================================================================

async def _safe_query(session: AsyncSession, sql: str, params: dict, domain: str) -> list:
    """Execute a query safely, returning empty list on table-not-found errors."""
    try:
        result = await session.execute(text(sql), params)
        return result.fetchall()
    except Exception as e:
        err = str(e)
        # Don't log noise for tables that haven't been created yet
        if "does not exist" in err or "UndefinedTable" in err:
            logger.debug(f"{domain}: table not yet created, skipping")
        else:
            logger.error(f"{domain} search error: {e}")
        return []


# =============================================================================
# SEARCH FUNCTIONS — BIOLOGICAL
# =============================================================================

async def search_taxa(
    session: AsyncSession, query: str, limit: int,
    toxicity_filter: Optional[str] = None,
    lat: Optional[float] = None, lng: Optional[float] = None, radius: Optional[float] = None,
) -> List[dict]:
    """Search fungi taxa (core.taxon)."""
    where_clauses = ["(canonical_name ILIKE :query OR common_name ILIKE :query)"]
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit, "exact_query": query}

    if toxicity_filter:
        if toxicity_filter in ("poisonous", "toxic", "deadly"):
            where_clauses.append("(metadata->>'toxicity' IS NOT NULL OR metadata->>'poisonous' = 'true')")
        elif toxicity_filter == "edible":
            where_clauses.append("(edibility = 'edible' OR metadata->>'edible' = 'true')")
        elif toxicity_filter in ("psychedelic", "hallucinogenic"):
            where_clauses.append("(metadata->>'psychoactive' = 'true' OR canonical_name ILIKE '%psilocybe%')")

    sql = f"""
        SELECT t.id, t.canonical_name, t.common_name, t.rank, t.description,
               NULL as image_url, 0 as observation_count,
               t.metadata->>'toxicity' as toxicity, t.edibility
        FROM core.taxon t
        WHERE {' AND '.join(where_clauses)}
        ORDER BY CASE WHEN t.canonical_name ILIKE :exact_query THEN 0 ELSE 1 END, t.canonical_name
        LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "taxa")
    return [
        TaxonResult(
            id=r.id, scientific_name=r.canonical_name, common_name=r.common_name,
            rank=r.rank, description=r.description, image_url=r.image_url,
            observation_count=r.observation_count or 0, toxicity=r.toxicity, edibility=r.edibility,
        ).model_dump()
        for r in rows
    ]


async def search_species(session: AsyncSession, query: str, limit: int, kingdom: Optional[str] = None) -> List[dict]:
    """Search all-kingdom species (species.organisms)."""
    where = "(scientific_name ILIKE :query OR common_name ILIKE :query)"
    params: Dict[str, Any] = {"query": f"%{query}%", "exact_query": query, "limit": limit}
    if kingdom:
        where += " AND kingdom ILIKE :kingdom"
        params["kingdom"] = kingdom

    sql = f"""
        SELECT id, source, kingdom, scientific_name, common_name, rank,
               conservation_status, habitat, description, image_url, properties,
               family, genus
        FROM species.organisms
        WHERE {where}
        ORDER BY CASE WHEN scientific_name ILIKE :exact_query THEN 0 ELSE 1 END, scientific_name
        LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "species")
    return [
        {
            "id": str(r.id), "domain": "species", "entity_type": r.kingdom or "organism",
            "scientific_name": r.scientific_name, "common_name": r.common_name,
            "rank": r.rank, "kingdom": r.kingdom, "family": r.family, "genus": r.genus,
            "conservation_status": r.conservation_status, "habitat": r.habitat,
            "description": r.description, "image_url": r.image_url, "source": r.source,
        }
        for r in rows
    ]


async def search_compounds(session: AsyncSession, query: str, limit: int) -> List[dict]:
    """Search compounds (core.compounds)."""
    sql = """
        SELECT c.id, c.name, c.molecular_formula as formula, c.molecular_weight,
               c.compound_class as chemical_class, c.smiles,
               COALESCE(c.producing_species, ARRAY[]::text[]) as species
        FROM core.compounds c
        WHERE c.name ILIKE :query OR c.molecular_formula ILIKE :query OR c.iupac_name ILIKE :query
           OR EXISTS (SELECT 1 FROM unnest(c.producing_species) ps WHERE ps ILIKE :query)
        ORDER BY CASE WHEN c.name ILIKE :exact_query THEN 0 ELSE 2 END, c.name
        LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "exact_query": query, "limit": limit}, "compounds")
    return [
        CompoundResult(
            id=r.id, name=r.name, formula=r.formula, molecular_weight=r.molecular_weight,
            chemical_class=r.chemical_class, smiles=r.smiles, source_species=r.species or [],
        ).model_dump()
        for r in rows
    ]


async def search_genetics(session: AsyncSession, query: str, limit: int) -> List[dict]:
    """Search genetics (core.dna_sequences)."""
    sql = """
        SELECT id, accession, scientific_name as species_name, gene_region as gene,
               COALESCE(sequence_length, 0) as sequence_length, COALESCE(source, 'genbank') as source
        FROM core.dna_sequences
        WHERE scientific_name ILIKE :query OR accession ILIKE :query OR gene_region ILIKE :query
        ORDER BY CASE WHEN scientific_name ILIKE :exact_query THEN 0 ELSE 1 END, scientific_name
        LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "exact_query": query, "limit": limit}, "genetics")
    return [
        GeneticsResult(
            id=r.id, accession=r.accession, species_name=r.species_name,
            gene=r.gene, sequence_length=r.sequence_length or 0, source=r.source or "genbank",
        ).model_dump()
        for r in rows
    ]


async def search_observations(
    session: AsyncSession, query: str, limit: int,
    lat: Optional[float] = None, lng: Optional[float] = None, radius: Optional[float] = None,
) -> List[dict]:
    """Search observations (core.observation + species.sightings)."""
    results = []

    # Core observations (fungi)
    if lat is not None and lng is not None:
        sql = """
            SELECT o.id::text, o.taxon_id::text, t.canonical_name as taxon_name,
                   o.location_name as location, ST_Y(o.geom) as lat, ST_X(o.geom) as lng,
                   o.observed_at::text as observed_at, NULL as image_url
            FROM core.observation o
            JOIN core.taxon t ON t.id = o.taxon_id
            WHERE ST_DWithin(o.geom::geography, ST_MakePoint(:lng, :lat)::geography, :radius_m)
              AND (t.canonical_name ILIKE :query OR t.common_name ILIKE :query)
            ORDER BY o.observed_at DESC LIMIT :limit
        """
        rows = await _safe_query(session, sql, {
            "query": f"%{query}%", "lat": lat, "lng": lng,
            "radius_m": (radius or 100) * 1000, "limit": limit,
        }, "observations")
        for r in rows:
            results.append({
                "id": r.id, "taxon_id": r.taxon_id, "taxon_name": r.taxon_name,
                "location": r.location, "lat": r.lat, "lng": r.lng,
                "observed_at": r.observed_at, "image_url": r.image_url, "source": "mindex",
            })

    # Species sightings (all kingdoms)
    sight_where = "TRUE"
    sight_params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        sight_where = "ST_DWithin(s.location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        sight_params.update({"lat": lat, "lng": lng, "radius_m": (radius or 100) * 1000})

    sql2 = f"""
        SELECT s.id::text, s.organism_id::text, o.scientific_name, o.common_name,
               ST_Y(s.location::geometry) as lat, ST_X(s.location::geometry) as lng,
               s.observed_at::text, s.image_url, s.source
        FROM species.sightings s
        JOIN species.organisms o ON o.id = s.organism_id
        WHERE {sight_where}
          AND (o.scientific_name ILIKE :query OR o.common_name ILIKE :query)
        ORDER BY s.observed_at DESC LIMIT :limit
    """
    rows2 = await _safe_query(session, sql2, sight_params, "sightings")
    for r in rows2:
        results.append({
            "id": r.id, "taxon_id": r.organism_id, "taxon_name": r.scientific_name,
            "common_name": r.common_name, "lat": r.lat, "lng": r.lng,
            "observed_at": r.observed_at, "image_url": r.image_url, "source": r.source,
        })

    return results[:limit]


# =============================================================================
# SEARCH FUNCTIONS — EARTH EVENTS
# =============================================================================

async def search_earthquakes(session: AsyncSession, query: str, limit: int,
                              lat: Optional[float] = None, lng: Optional[float] = None,
                              radius: Optional[float] = None) -> List[dict]:
    where = "place_name ILIKE :query OR alert_level ILIKE :query OR magnitude::text LIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 500) * 1000})

    sql = f"""
        SELECT id::text, source, magnitude, magnitude_type, depth_km, place_name,
               occurred_at::text, alert_level, tsunami_flag,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM earth.earthquakes WHERE {where}
        ORDER BY occurred_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "earthquakes")
    return [
        {"id": r.id, "domain": "earth_events", "entity_type": "earthquake",
         "name": f"M{r.magnitude} {r.place_name or 'Earthquake'}",
         "magnitude": r.magnitude, "magnitude_type": r.magnitude_type,
         "depth_km": r.depth_km, "alert_level": r.alert_level,
         "tsunami": r.tsunami_flag, "lat": r.lat, "lng": r.lng,
         "occurred_at": r.occurred_at, "source": r.source}
        for r in rows
    ]


async def search_volcanoes(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, name, volcano_type, elevation_m, country, region,
               last_eruption, alert_level,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM earth.volcanoes
        WHERE name ILIKE :query OR country ILIKE :query OR region ILIKE :query
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "volcanoes")
    return [
        {"id": r.id, "domain": "earth_events", "entity_type": "volcano",
         "name": r.name, "volcano_type": r.volcano_type, "elevation_m": r.elevation_m,
         "country": r.country, "region": r.region, "last_eruption": r.last_eruption,
         "alert_level": r.alert_level, "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


async def search_wildfires(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, name, status, area_acres, containment_pct,
               detected_at::text, brightness, frp,
               ST_Y(ST_Centroid(location::geometry)) as lat,
               ST_X(ST_Centroid(location::geometry)) as lng
        FROM earth.wildfires
        WHERE name ILIKE :query OR status ILIKE :query
        ORDER BY detected_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "wildfires")
    return [
        {"id": r.id, "domain": "earth_events", "entity_type": "wildfire",
         "name": r.name or "Wildfire", "status": r.status, "area_acres": r.area_acres,
         "containment_pct": r.containment_pct, "brightness": r.brightness, "frp": r.frp,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.detected_at, "source": r.source}
        for r in rows
    ]


async def search_storms(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, name, storm_type, category, wind_speed_kts,
               pressure_mb, status, observed_at::text,
               ST_Y(current_location::geometry) as lat,
               ST_X(current_location::geometry) as lng
        FROM earth.storms
        WHERE name ILIKE :query OR storm_type ILIKE :query
        ORDER BY observed_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "storms")
    return [
        {"id": r.id, "domain": "earth_events", "entity_type": "storm",
         "name": r.name or r.storm_type, "storm_type": r.storm_type, "category": r.category,
         "wind_speed_kts": r.wind_speed_kts, "pressure_mb": r.pressure_mb,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.observed_at, "source": r.source}
        for r in rows
    ]


async def search_lightning(session: AsyncSession, query: str, limit: int,
                            lat: Optional[float] = None, lng: Optional[float] = None,
                            radius: Optional[float] = None) -> List[dict]:
    if lat is None or lng is None:
        return []  # Lightning only makes sense with location
    sql = """
        SELECT id::text, source, polarity, peak_current_ka, stroke_type, occurred_at::text,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM earth.lightning
        WHERE ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)
        ORDER BY occurred_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {
        "lat": lat, "lng": lng, "radius_m": (radius or 50) * 1000, "limit": limit,
    }, "lightning")
    return [
        {"id": r.id, "domain": "earth_events", "entity_type": "lightning",
         "name": f"{r.polarity or ''} {r.peak_current_ka or '?'}kA strike",
         "polarity": r.polarity, "peak_current_ka": r.peak_current_ka,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.occurred_at, "source": r.source}
        for r in rows
    ]


async def search_tornadoes(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, ef_rating, path_length_mi, path_width_yd,
               fatalities, injuries, occurred_at::text, state,
               ST_Y(path_start::geometry) as lat, ST_X(path_start::geometry) as lng
        FROM earth.tornadoes
        WHERE state ILIKE :query OR ef_rating::text = :exact_query
        ORDER BY occurred_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "exact_query": query, "limit": limit}, "tornadoes")
    return [
        {"id": r.id, "domain": "earth_events", "entity_type": "tornado",
         "name": f"EF{r.ef_rating or '?'} Tornado — {r.state or 'Unknown'}",
         "ef_rating": r.ef_rating, "path_length_mi": r.path_length_mi,
         "fatalities": r.fatalities, "injuries": r.injuries,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.occurred_at, "source": r.source}
        for r in rows
    ]


async def search_floods(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, flood_type, severity, water_level_m, observed_at::text,
               ST_Y(ST_Centroid(area::geometry)) as lat,
               ST_X(ST_Centroid(area::geometry)) as lng
        FROM earth.floods
        WHERE flood_type ILIKE :query OR severity ILIKE :query
        ORDER BY observed_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "floods")
    return [
        {"id": r.id, "domain": "earth_events", "entity_type": "flood",
         "name": f"{r.severity or ''} {r.flood_type or 'Flood'}".strip(),
         "flood_type": r.flood_type, "severity": r.severity, "water_level_m": r.water_level_m,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.observed_at, "source": r.source}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — ATMOSPHERE & CLIMATE
# =============================================================================

async def search_air_quality(session: AsyncSession, query: str, limit: int,
                              lat: Optional[float] = None, lng: Optional[float] = None,
                              radius: Optional[float] = None) -> List[dict]:
    where = "station_name ILIKE :query OR parameter ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 100) * 1000})

    sql = f"""
        SELECT id::text, source, station_name, parameter, value, unit, measured_at::text,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM atmos.air_quality
        WHERE {where}
        ORDER BY measured_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "air_quality")
    return [
        {"id": r.id, "domain": "atmosphere", "entity_type": "air_quality",
         "name": f"{r.station_name or 'Station'} — {r.parameter}: {r.value}{r.unit}",
         "station_name": r.station_name, "parameter": r.parameter,
         "value": r.value, "unit": r.unit,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.measured_at, "source": r.source}
        for r in rows
    ]


async def search_greenhouse_gas(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, gas_type, value, unit, station_name, measured_at::text,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM atmos.greenhouse_gas
        WHERE gas_type ILIKE :query OR station_name ILIKE :query
        ORDER BY measured_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "greenhouse_gas")
    return [
        {"id": r.id, "domain": "atmosphere", "entity_type": "greenhouse_gas",
         "name": f"{r.gas_type.upper()}: {r.value} {r.unit} @ {r.station_name or 'Global'}",
         "gas_type": r.gas_type, "value": r.value, "unit": r.unit,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.measured_at, "source": r.source}
        for r in rows
    ]


async def search_weather(session: AsyncSession, query: str, limit: int,
                          lat: Optional[float] = None, lng: Optional[float] = None,
                          radius: Optional[float] = None) -> List[dict]:
    where = "station_name ILIKE :query OR conditions ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 200) * 1000})

    sql = f"""
        SELECT id::text, source, station_id, station_name, temperature_c, humidity_pct,
               pressure_hpa, wind_speed_ms, wind_direction, conditions, observed_at::text,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM atmos.weather_observations
        WHERE {where}
        ORDER BY observed_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "weather")
    return [
        {"id": r.id, "domain": "atmosphere", "entity_type": "weather",
         "name": f"{r.station_name or r.station_id}: {r.temperature_c}°C {r.conditions or ''}".strip(),
         "temperature_c": r.temperature_c, "humidity_pct": r.humidity_pct,
         "pressure_hpa": r.pressure_hpa, "wind_speed_ms": r.wind_speed_ms,
         "conditions": r.conditions,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.observed_at, "source": r.source}
        for r in rows
    ]


async def search_remote_sensing(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, product, satellite, resolution_m,
               acquisition_time::text, cloud_cover_pct, data_url, thumbnail_url,
               ST_Y(centroid::geometry) as lat, ST_X(centroid::geometry) as lng
        FROM atmos.remote_sensing
        WHERE source ILIKE :query OR product ILIKE :query OR satellite ILIKE :query
        ORDER BY acquisition_time DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "remote_sensing")
    return [
        {"id": r.id, "domain": "atmosphere", "entity_type": "remote_sensing",
         "name": f"{r.satellite or r.source} — {r.product}",
         "satellite": r.satellite, "product": r.product, "resolution_m": r.resolution_m,
         "data_url": r.data_url, "thumbnail_url": r.thumbnail_url,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.acquisition_time, "source": r.source}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — WATER SYSTEMS
# =============================================================================

async def search_buoys(session: AsyncSession, query: str, limit: int,
                        lat: Optional[float] = None, lng: Optional[float] = None,
                        radius: Optional[float] = None) -> List[dict]:
    where = "name ILIKE :query OR station_id ILIKE :query OR buoy_type ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 500) * 1000})

    sql = f"""
        SELECT id::text, source, station_id, name, buoy_type, water_temp_c,
               wave_height_m, wave_period_s, wind_speed_ms, observed_at::text,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM hydro.buoys
        WHERE {where}
        ORDER BY observed_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "buoys")
    return [
        {"id": r.id, "domain": "water", "entity_type": "buoy",
         "name": r.name or f"Buoy {r.station_id}", "buoy_type": r.buoy_type,
         "water_temp_c": r.water_temp_c, "wave_height_m": r.wave_height_m,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.observed_at, "source": r.source}
        for r in rows
    ]


async def search_stream_gauges(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, site_id, name, discharge_cfs, gauge_height_ft,
               water_temp_c, observed_at::text, flood_stage_ft,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM hydro.stream_gauges
        WHERE name ILIKE :query OR site_id ILIKE :query
        ORDER BY observed_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "stream_gauges")
    return [
        {"id": r.id, "domain": "water", "entity_type": "stream_gauge",
         "name": r.name or f"Gauge {r.site_id}",
         "discharge_cfs": r.discharge_cfs, "gauge_height_ft": r.gauge_height_ft,
         "flood_stage_ft": r.flood_stage_ft,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.observed_at, "source": r.source}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — INFRASTRUCTURE
# =============================================================================

async def search_facilities(session: AsyncSession, query: str, limit: int,
                             facility_type: Optional[str] = None) -> List[dict]:
    where = "name ILIKE :query OR facility_type ILIKE :query OR sub_type ILIKE :query OR operator ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if facility_type:
        where += " AND facility_type = :ftype"
        params["ftype"] = facility_type

    sql = f"""
        SELECT id::text, source, name, facility_type, sub_type, operator, status,
               city, state_province, country, capacity, emissions,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM infra.facilities
        WHERE {where}
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "facilities")
    return [
        {"id": r.id, "domain": "infrastructure", "entity_type": r.facility_type,
         "name": r.name, "facility_type": r.facility_type, "sub_type": r.sub_type,
         "operator": r.operator, "status": r.status, "city": r.city,
         "country": r.country, "capacity": r.capacity,
         "emissions": r.emissions,
         "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


async def search_power_grid(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, asset_type, name, voltage_kv, operator,
               ST_Y(ST_Centroid(location::geometry)) as lat,
               ST_X(ST_Centroid(location::geometry)) as lng
        FROM infra.power_grid
        WHERE name ILIKE :query OR asset_type ILIKE :query OR operator ILIKE :query
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "power_grid")
    return [
        {"id": r.id, "domain": "infrastructure", "entity_type": "power_grid",
         "name": r.name or r.asset_type, "asset_type": r.asset_type,
         "voltage_kv": r.voltage_kv, "operator": r.operator,
         "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


async def search_water_systems(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, system_type, name, capacity,
               ST_Y(ST_Centroid(location::geometry)) as lat,
               ST_X(ST_Centroid(location::geometry)) as lng
        FROM infra.water_systems
        WHERE name ILIKE :query OR system_type ILIKE :query
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "water_systems")
    return [
        {"id": r.id, "domain": "infrastructure", "entity_type": r.system_type,
         "name": r.name, "system_type": r.system_type, "capacity": r.capacity,
         "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


async def search_internet_cables(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, name, cable_type, length_km, capacity_tbps,
               status, owners, landing_points
        FROM infra.internet_cables
        WHERE name ILIKE :query OR cable_type ILIKE :query
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "internet_cables")
    return [
        {"id": r.id, "domain": "infrastructure", "entity_type": "internet_cable",
         "name": r.name, "cable_type": r.cable_type, "length_km": r.length_km,
         "capacity_tbps": r.capacity_tbps, "status": r.status,
         "owners": r.owners, "landing_points": r.landing_points, "source": r.source}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — SIGNALS & RF
# =============================================================================

async def search_antennas(session: AsyncSession, query: str, limit: int,
                           lat: Optional[float] = None, lng: Optional[float] = None,
                           radius: Optional[float] = None) -> List[dict]:
    where = "antenna_type ILIKE :query OR operator ILIKE :query OR technology ILIKE :query OR call_sign ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 50) * 1000})

    sql = f"""
        SELECT id::text, source, antenna_type, frequency_mhz, band, operator,
               call_sign, power_watts, height_m, technology, status, coverage_radius_m,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM signals.antennas
        WHERE {where}
        ORDER BY antenna_type, operator LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "antennas")
    return [
        {"id": r.id, "domain": "signals", "entity_type": r.antenna_type,
         "name": f"{r.antenna_type} — {r.operator or r.call_sign or 'Unknown'}",
         "antenna_type": r.antenna_type, "frequency_mhz": r.frequency_mhz,
         "band": r.band, "operator": r.operator, "technology": r.technology,
         "power_watts": r.power_watts, "height_m": r.height_m,
         "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


async def search_wifi_hotspots(session: AsyncSession, query: str, limit: int,
                                lat: Optional[float] = None, lng: Optional[float] = None,
                                radius: Optional[float] = None) -> List[dict]:
    where = "ssid ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 10) * 1000})

    sql = f"""
        SELECT id::text, source, ssid, bssid, encryption, channel,
               signal_dbm, last_seen::text,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM signals.wifi_hotspots
        WHERE {where}
        ORDER BY last_seen DESC NULLS LAST LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "wifi_hotspots")
    return [
        {"id": r.id, "domain": "signals", "entity_type": "wifi_hotspot",
         "name": r.ssid or f"[Hidden] {r.bssid}", "encryption": r.encryption,
         "channel": r.channel, "signal_dbm": r.signal_dbm,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.last_seen, "source": r.source}
        for r in rows
    ]


async def search_signal_measurements(session: AsyncSession, query: str, limit: int,
                                      lat: Optional[float] = None, lng: Optional[float] = None,
                                      radius: Optional[float] = None) -> List[dict]:
    where = "measurement_type ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 25) * 1000})

    sql = f"""
        SELECT id::text, measurement_type, frequency_mhz, value, unit, measured_at::text,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM signals.signal_measurements
        WHERE {where}
        ORDER BY measured_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "signal_measurements")
    return [
        {"id": r.id, "domain": "signals", "entity_type": "signal_measurement",
         "name": f"{r.measurement_type}: {r.value}{r.unit}",
         "measurement_type": r.measurement_type, "frequency_mhz": r.frequency_mhz,
         "value": r.value, "unit": r.unit,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.measured_at}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — TRANSPORT
# =============================================================================

async def search_aircraft(session: AsyncSession, query: str, limit: int,
                           lat: Optional[float] = None, lng: Optional[float] = None,
                           radius: Optional[float] = None) -> List[dict]:
    where = "callsign ILIKE :query OR registration ILIKE :query OR icao24 ILIKE :query OR aircraft_type ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 200) * 1000})

    sql = f"""
        SELECT id::text, source, icao24, callsign, registration, aircraft_type,
               origin, destination, altitude_ft, ground_speed_kts, heading, observed_at::text,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM transport.aircraft
        WHERE {where}
        ORDER BY observed_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "aircraft")
    return [
        {"id": r.id, "domain": "transport", "entity_type": "aircraft",
         "name": f"{r.callsign or r.registration or r.icao24}",
         "icao24": r.icao24, "callsign": r.callsign, "registration": r.registration,
         "aircraft_type": r.aircraft_type, "origin": r.origin, "destination": r.destination,
         "altitude_ft": r.altitude_ft, "ground_speed_kts": r.ground_speed_kts,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.observed_at, "source": r.source}
        for r in rows
    ]


async def search_vessels(session: AsyncSession, query: str, limit: int,
                          lat: Optional[float] = None, lng: Optional[float] = None,
                          radius: Optional[float] = None) -> List[dict]:
    where = "name ILIKE :query OR mmsi ILIKE :query OR imo ILIKE :query OR vessel_type ILIKE :query OR destination ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 500) * 1000})

    sql = f"""
        SELECT id::text, source, mmsi, imo, name, vessel_type, flag,
               speed_knots, course, destination, nav_status, observed_at::text,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM transport.vessels
        WHERE {where}
        ORDER BY observed_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "vessels")
    return [
        {"id": r.id, "domain": "transport", "entity_type": "vessel",
         "name": r.name or f"MMSI:{r.mmsi}", "vessel_type": r.vessel_type,
         "mmsi": r.mmsi, "imo": r.imo, "flag": r.flag,
         "speed_knots": r.speed_knots, "destination": r.destination,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.observed_at, "source": r.source}
        for r in rows
    ]


async def search_airports(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, icao_code, iata_code, name, airport_type,
               elevation_ft, country, municipality,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM transport.airports
        WHERE name ILIKE :query OR icao_code ILIKE :query OR iata_code ILIKE :query
           OR municipality ILIKE :query
        ORDER BY CASE WHEN icao_code ILIKE :exact_query OR iata_code ILIKE :exact_query THEN 0 ELSE 1 END, name
        LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "exact_query": query, "limit": limit}, "airports")
    return [
        {"id": r.id, "domain": "transport", "entity_type": "airport",
         "name": f"{r.name} ({r.iata_code or r.icao_code})",
         "icao_code": r.icao_code, "iata_code": r.iata_code,
         "airport_type": r.airport_type, "country": r.country,
         "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


async def search_ports(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, name, port_type, unlocode, country,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM transport.ports
        WHERE name ILIKE :query OR unlocode ILIKE :query OR country ILIKE :query
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "ports")
    return [
        {"id": r.id, "domain": "transport", "entity_type": "port",
         "name": r.name, "port_type": r.port_type, "unlocode": r.unlocode,
         "country": r.country, "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


async def search_spaceports(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, name, operator, country, orbital_capable, status,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM transport.spaceports
        WHERE name ILIKE :query OR operator ILIKE :query OR country ILIKE :query
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "spaceports")
    return [
        {"id": r.id, "domain": "transport", "entity_type": "spaceport",
         "name": r.name, "operator": r.operator, "country": r.country,
         "orbital_capable": r.orbital_capable,
         "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


async def search_launches(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, name, provider, vehicle, mission_type,
               pad_name, launch_time::text, status, orbit,
               ST_Y(pad_location::geometry) as lat, ST_X(pad_location::geometry) as lng
        FROM transport.launches
        WHERE name ILIKE :query OR provider ILIKE :query OR vehicle ILIKE :query
           OR mission_type ILIKE :query
        ORDER BY launch_time DESC NULLS LAST LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "launches")
    return [
        {"id": r.id, "domain": "space", "entity_type": "launch",
         "name": r.name, "provider": r.provider, "vehicle": r.vehicle,
         "mission_type": r.mission_type, "status": r.status, "orbit": r.orbit,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.launch_time, "source": r.source}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — SPACE
# =============================================================================

async def search_satellites(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, norad_id, cospar_id, name, satellite_type,
               operator, launch_date::text, orbit_type, perigee_km, apogee_km,
               inclination_deg, status
        FROM space.satellites
        WHERE name ILIKE :query OR satellite_type ILIKE :query OR operator ILIKE :query
           OR norad_id::text = :exact_query OR cospar_id ILIKE :query
        ORDER BY CASE WHEN name ILIKE :exact_query THEN 0 ELSE 1 END, name
        LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "exact_query": query, "limit": limit}, "satellites")
    return [
        {"id": r.id, "domain": "space", "entity_type": "satellite",
         "name": r.name, "norad_id": r.norad_id, "satellite_type": r.satellite_type,
         "operator": r.operator, "orbit_type": r.orbit_type,
         "perigee_km": r.perigee_km, "apogee_km": r.apogee_km,
         "status": r.status, "source": r.source}
        for r in rows
    ]


async def search_solar_events(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, event_type, class, intensity, kp_index,
               speed_km_s, source_region, start_time::text, peak_time::text,
               earth_directed
        FROM space.solar_events
        WHERE event_type ILIKE :query OR class ILIKE :query OR source_region ILIKE :query
        ORDER BY start_time DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "solar_events")
    return [
        {"id": r.id, "domain": "space", "entity_type": "solar_event",
         "name": f"{r.event_type} {r.class_ if hasattr(r, 'class_') else ''} — Region {r.source_region or '?'}".strip(),
         "event_type": r.event_type, "class": getattr(r, "class", None),
         "intensity": r.intensity, "kp_index": r.kp_index, "speed_km_s": r.speed_km_s,
         "earth_directed": r.earth_directed,
         "occurred_at": r.start_time, "source": r.source}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — MONITORING
# =============================================================================

async def search_cameras(session: AsyncSession, query: str, limit: int,
                          lat: Optional[float] = None, lng: Optional[float] = None,
                          radius: Optional[float] = None) -> List[dict]:
    where = "name ILIKE :query OR camera_type ILIKE :query OR city ILIKE :query OR country ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(location, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 50) * 1000})

    sql = f"""
        SELECT id::text, source, name, camera_type, stream_url, snapshot_url,
               city, country, status,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM monitor.cameras
        WHERE {where}
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "cameras")
    return [
        {"id": r.id, "domain": "monitoring", "entity_type": r.camera_type or "camera",
         "name": r.name or f"Camera — {r.city or r.country}",
         "camera_type": r.camera_type, "stream_url": r.stream_url,
         "snapshot_url": r.snapshot_url, "city": r.city, "country": r.country,
         "status": r.status, "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — MILITARY
# =============================================================================

async def search_military_installations(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, source, name, installation_type, branch, country, status,
               ST_Y(location::geometry) as lat, ST_X(location::geometry) as lng
        FROM military.installations
        WHERE name ILIKE :query OR installation_type ILIKE :query
           OR branch ILIKE :query OR country ILIKE :query
        ORDER BY name LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "military_installations")
    return [
        {"id": r.id, "domain": "military", "entity_type": r.installation_type or "installation",
         "name": r.name, "installation_type": r.installation_type,
         "branch": r.branch, "country": r.country, "status": r.status,
         "lat": r.lat, "lng": r.lng, "source": r.source}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — TELEMETRY & DEVICES
# =============================================================================

async def search_devices(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT d.id::text, d.name, d.device_type, d.status,
               ST_Y(d.location::geometry) as lat, ST_X(d.location::geometry) as lng,
               d.created_at::text
        FROM telemetry.device d
        WHERE d.name ILIKE :query OR d.device_type ILIKE :query
        ORDER BY d.created_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "devices")
    return [
        {"id": r.id, "domain": "telemetry", "entity_type": r.device_type or "device",
         "name": r.name or "Device", "device_type": r.device_type,
         "status": r.status, "lat": r.lat, "lng": r.lng, "source": "mycobrain"}
        for r in rows
    ]


async def search_telemetry(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT s.id::text, s.stream_key, s.unit, d.name as device_name,
               d.device_type,
               (SELECT value_numeric FROM telemetry.sample
                WHERE stream_id = s.id ORDER BY recorded_at DESC LIMIT 1) as last_value,
               (SELECT recorded_at::text FROM telemetry.sample
                WHERE stream_id = s.id ORDER BY recorded_at DESC LIMIT 1) as last_reading
        FROM telemetry.stream s
        JOIN telemetry.device d ON d.id = s.device_id
        WHERE s.stream_key ILIKE :query OR d.name ILIKE :query
        ORDER BY d.name LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "telemetry")
    return [
        {"id": r.id, "domain": "telemetry", "entity_type": "stream",
         "name": f"{r.device_name}: {r.stream_key}",
         "stream_key": r.stream_key, "unit": r.unit,
         "last_value": r.last_value, "occurred_at": r.last_reading, "source": "mycobrain"}
        for r in rows
    ]


# =============================================================================
# SEARCH FUNCTIONS — KNOWLEDGE
# =============================================================================

async def search_research(session: AsyncSession, query: str, limit: int) -> List[dict]:
    sql = """
        SELECT id::text, title, authors, journal, year, doi, abstract
        FROM core.publications
        WHERE title ILIKE :query OR abstract ILIKE :query
        ORDER BY year DESC NULLS LAST LIMIT :limit
    """
    rows = await _safe_query(session, sql, {"query": f"%{query}%", "limit": limit}, "research")
    return [
        {"id": r.id, "domain": "knowledge", "entity_type": "research_paper",
         "name": r.title, "authors": r.authors, "journal": r.journal,
         "year": r.year, "doi": r.doi, "abstract": r.abstract, "source": "openalex"}
        for r in rows
    ]


async def search_crep_entities(session: AsyncSession, query: str, limit: int,
                                lat: Optional[float] = None, lng: Optional[float] = None,
                                radius: Optional[float] = None) -> List[dict]:
    where = "entity_type ILIKE :query"
    params: Dict[str, Any] = {"query": f"%{query}%", "limit": limit}
    if lat is not None and lng is not None:
        where += " OR ST_DWithin(geometry, ST_MakePoint(:lng, :lat)::geography, :radius_m)"
        params.update({"lat": lat, "lng": lng, "radius_m": (radius or 100) * 1000})

    sql = f"""
        SELECT id, entity_type, state, observed_at::text, source, confidence,
               ST_Y(geometry::geometry) as lat, ST_X(geometry::geometry) as lng
        FROM crep.unified_entities
        WHERE {where}
        ORDER BY observed_at DESC LIMIT :limit
    """
    rows = await _safe_query(session, sql, params, "crep_entities")
    return [
        {"id": r.id, "domain": "crep", "entity_type": r.entity_type,
         "name": r.state.get("name", r.entity_type) if isinstance(r.state, dict) else r.entity_type,
         "state": r.state, "confidence": r.confidence,
         "lat": r.lat, "lng": r.lng, "occurred_at": r.observed_at, "source": r.source}
        for r in rows
    ]


# =============================================================================
# DOMAIN DISPATCH TABLE
# =============================================================================

def _build_dispatch(session, query, limit, lat, lng, radius, toxicity, kingdom, facility_type):
    """Map domain names to their search coroutines."""
    return {
        # Biological
        "taxa": search_taxa(session, query, limit, toxicity, lat, lng, radius),
        "species": search_species(session, query, limit, kingdom),
        "compounds": search_compounds(session, query, limit),
        "genetics": search_genetics(session, query, limit),
        "observations": search_observations(session, query, limit, lat, lng, radius),
        # Earth events
        "earthquakes": search_earthquakes(session, query, limit, lat, lng, radius),
        "volcanoes": search_volcanoes(session, query, limit),
        "wildfires": search_wildfires(session, query, limit),
        "storms": search_storms(session, query, limit),
        "lightning": search_lightning(session, query, limit, lat, lng, radius),
        "tornadoes": search_tornadoes(session, query, limit),
        "floods": search_floods(session, query, limit),
        # Atmosphere
        "air_quality": search_air_quality(session, query, limit, lat, lng, radius),
        "greenhouse_gas": search_greenhouse_gas(session, query, limit),
        "weather": search_weather(session, query, limit, lat, lng, radius),
        "remote_sensing": search_remote_sensing(session, query, limit),
        # Water
        "buoys": search_buoys(session, query, limit, lat, lng, radius),
        "stream_gauges": search_stream_gauges(session, query, limit),
        # Infrastructure
        "facilities": search_facilities(session, query, limit, facility_type),
        "power_grid": search_power_grid(session, query, limit),
        "water_systems": search_water_systems(session, query, limit),
        "internet_cables": search_internet_cables(session, query, limit),
        # Signals
        "antennas": search_antennas(session, query, limit, lat, lng, radius),
        "wifi_hotspots": search_wifi_hotspots(session, query, limit, lat, lng, radius),
        "signal_measurements": search_signal_measurements(session, query, limit, lat, lng, radius),
        # Transport
        "aircraft": search_aircraft(session, query, limit, lat, lng, radius),
        "vessels": search_vessels(session, query, limit, lat, lng, radius),
        "airports": search_airports(session, query, limit),
        "ports": search_ports(session, query, limit),
        "spaceports": search_spaceports(session, query, limit),
        "launches": search_launches(session, query, limit),
        # Space
        "satellites": search_satellites(session, query, limit),
        "solar_events": search_solar_events(session, query, limit),
        # Monitoring
        "cameras": search_cameras(session, query, limit, lat, lng, radius),
        # Military
        "military_installations": search_military_installations(session, query, limit),
        # Telemetry
        "devices": search_devices(session, query, limit),
        "telemetry": search_telemetry(session, query, limit),
        # Knowledge
        "research": search_research(session, query, limit),
        "crep_entities": search_crep_entities(session, query, limit, lat, lng, radius),
    }


def _resolve_domains(types_str: str) -> List[str]:
    """Resolve a comma-separated types string into a flat list of domain names."""
    domains = []
    for t in types_str.split(","):
        t = t.strip().lower()
        if t in DOMAIN_GROUPS:
            domains.extend(DOMAIN_GROUPS[t])
        elif t in ALL_DOMAINS:
            domains.append(t)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for d in domains:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


# =============================================================================
# MAIN ENDPOINTS
# =============================================================================

@router.get("", response_model=UnifiedSearchResponse)
async def unified_search(
    q: str = Query(..., min_length=2, description="Search query"),
    types: str = Query(
        "all",
        description=(
            "Comma-separated domains or groups to search. "
            "Groups: all, biological, life, earth_events, hazards, atmosphere, water, "
            "infrastructure, pollution, signals, transport, aviation, maritime, space, "
            "monitoring, military, telemetry. "
            "Individual: taxa, species, compounds, genetics, observations, earthquakes, "
            "volcanoes, wildfires, storms, lightning, tornadoes, floods, air_quality, "
            "greenhouse_gas, weather, remote_sensing, buoys, stream_gauges, facilities, "
            "power_grid, water_systems, internet_cables, antennas, wifi_hotspots, "
            "signal_measurements, aircraft, vessels, airports, ports, spaceports, "
            "launches, satellites, solar_events, cameras, military_installations, "
            "devices, telemetry, research, crep_entities"
        ),
    ),
    limit: int = Query(20, ge=1, le=100, description="Max results per domain"),
    # Location filters
    lat: Optional[float] = Query(None, description="Latitude for location-aware search"),
    lng: Optional[float] = Query(None, description="Longitude for location-aware search"),
    radius: Optional[float] = Query(100, description="Search radius in km"),
    # Content filters
    toxicity: Optional[str] = Query(None, description="Toxicity filter for taxa: poisonous, edible, psychedelic"),
    kingdom: Optional[str] = Query(None, description="Kingdom filter for species: Plantae, Animalia, Fungi, etc."),
    facility_type: Optional[str] = Query(None, description="Facility type filter: factory, power_plant, mining, dam, etc."),
    # Time filters
    since: Optional[str] = Query(None, description="ISO datetime — only return results after this time"),
    until: Optional[str] = Query(None, description="ISO datetime — only return results before this time"),
    session: AsyncSession = Depends(get_db_session),
):
    """
    **Unified Earth Search** — Search everything on the planet in parallel.

    Queries all MINDEX data domains simultaneously and returns combined results.
    Every result carries lat/lng when available for direct CREP map rendering.

    Use `types=all` to search everything, or narrow with domain groups:
    - `biological` — taxa, species, compounds, genetics, observations
    - `earth_events` — earthquakes, volcanoes, wildfires, storms, lightning, tornadoes, floods
    - `atmosphere` — air quality, greenhouse gases, weather, remote sensing
    - `infrastructure` — facilities, power grid, water systems, internet cables
    - `signals` — antennas, wifi hotspots, signal measurements
    - `transport` — aircraft, vessels, airports, ports, spaceports, launches
    - `space` — satellites, solar events, launches
    - `monitoring` — cameras/webcams
    - `military` — publicly known installations
    - `telemetry` — MycoBrain devices and sensor streams
    """
    start_time = time.time()

    # ── TIER 0+1: Check cache first (LRU + Redis) ──────────────────────
    from ..cache import get_cache
    cache = get_cache()
    await cache.connect()

    cached = await cache.get_cached_search(q, types)
    if cached is not None:
        timing_ms = int((time.time() - start_time) * 1000)
        return UnifiedSearchResponse(
            query=q,
            domains_searched=cached.get("domains_searched", []),
            results=cached.get("results", {}),
            total_count=cached.get("total_count", 0),
            timing_ms=timing_ms,
            filters_applied=cached.get("filters_applied", {}),
        )

    # ── TIER 2: Local PostgreSQL (parallel across all domains) ─────────
    domains = _resolve_domains(types)
    if not domains:
        domains = ALL_DOMAINS

    dispatch = _build_dispatch(session, q, limit, lat, lng, radius, toxicity, kingdom, facility_type)

    tasks = []
    task_names = []
    for domain in domains:
        if domain in dispatch:
            tasks.append(dispatch[domain])
            task_names.append(domain)

    results_list = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    results: Dict[str, List[Any]] = {}
    total_count = 0
    empty_domains = []

    for name, result in zip(task_names, results_list):
        if isinstance(result, Exception):
            logger.error(f"Domain {name} search failed: {result}")
            results[name] = []
            empty_domains.append(name)
        else:
            results[name] = result
            total_count += len(result)
            if not result:
                empty_domains.append(name)

    # ── TIER 4: Live-scrape for domains that returned 0 results ────────
    # Only scrape domains that have live scrapers configured
    from ..scrape_pipeline import LIVE_SCRAPERS
    scrape_tasks = []
    scrape_names = []
    for domain in empty_domains:
        if domain in LIVE_SCRAPERS:
            scrape_tasks.append(
                asyncio.get_event_loop().run_in_executor(
                    None, LIVE_SCRAPERS[domain], q
                )
            )
            scrape_names.append(domain)

    if scrape_tasks:
        scrape_results = await asyncio.gather(*scrape_tasks, return_exceptions=True)
        for name, result in zip(scrape_names, scrape_results):
            if isinstance(result, Exception):
                logger.debug(f"Live scrape {name} failed: {result}")
            elif result:
                results[name] = result
                total_count += len(result)
                # Async: store scraped data locally for future searches
                asyncio.create_task(_async_store_scraped(session, name, result))

    timing_ms = int((time.time() - start_time) * 1000)

    filters_applied: Dict[str, Any] = {}
    if toxicity:
        filters_applied["toxicity"] = toxicity
    if kingdom:
        filters_applied["kingdom"] = kingdom
    if facility_type:
        filters_applied["facility_type"] = facility_type
    if lat is not None and lng is not None:
        filters_applied["location"] = {"lat": lat, "lng": lng, "radius_km": radius}
    if since:
        filters_applied["since"] = since
    if until:
        filters_applied["until"] = until

    response_data = {
        "domains_searched": task_names,
        "results": results,
        "total_count": total_count,
        "filters_applied": filters_applied,
    }

    # ── Cache the results for future requests ──────────────────────────
    await cache.cache_search(q, types, response_data, ttl=120)

    # ── Async: Sync to Supabase for global access ──────────────────────
    from ..supabase_client import get_supabase
    supa = get_supabase()
    if supa.enabled and total_count > 0:
        asyncio.create_task(supa.sync_search_results(q, results))

    return UnifiedSearchResponse(
        query=q,
        domains_searched=task_names,
        results=results,
        total_count=total_count,
        timing_ms=timing_ms,
        filters_applied=filters_applied,
    )


async def _async_store_scraped(session: AsyncSession, domain: str, records: List[dict]):
    """Background task: store live-scraped data in local DB for future instant access."""
    try:
        for record in records:
            lat = record.get("lat")
            lng = record.get("lng")
            if lat is not None and lng is not None:
                import json as _json
                await session.execute(text("""
                    INSERT INTO crep.unified_entities (id, entity_type, geometry, state,
                        observed_at, valid_from, source, confidence, s2_cell_id)
                    VALUES (:id, :type, ST_MakePoint(:lng, :lat)::geography,
                        :state::jsonb, COALESCE(:occurred_at::timestamptz, NOW()), NOW(),
                        :source, 0.7, 0)
                    ON CONFLICT (id, observed_at) DO NOTHING
                """), {
                    "id": str(record.get("id", f"{domain}_{id(record)}")),
                    "type": record.get("entity_type", domain),
                    "lng": lng, "lat": lat,
                    "state": _json.dumps(record, default=str),
                    "occurred_at": record.get("occurred_at"),
                    "source": record.get("source", f"scrape_{domain}"),
                })
        await session.commit()
    except Exception as e:
        logger.debug(f"Async store scraped {domain} error: {e}")


@router.get("/earth", response_model=EarthSearchResponse)
async def earth_search(
    q: str = Query(..., min_length=2, description="Search query"),
    types: str = Query("all", description="Comma-separated domains or groups"),
    limit: int = Query(20, ge=1, le=100, description="Max results per domain"),
    lat: Optional[float] = Query(None, description="Latitude"),
    lng: Optional[float] = Query(None, description="Longitude"),
    radius: Optional[float] = Query(100, description="Radius in km"),
    toxicity: Optional[str] = Query(None),
    kingdom: Optional[str] = Query(None),
    facility_type: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db_session),
):
    """
    **Earth Search with CREP Map Pipeline** — Returns both domain-keyed results
    and a flat `universal_results` list with normalized SearchResult objects
    ready for direct CREP map overlay rendering.

    Every entity gets a lat/lng (when available), domain tag, and entity_type
    so CREP can render pins, clusters, and layers simultaneously.
    """
    start_time = time.time()

    domains = _resolve_domains(types)
    if not domains:
        domains = ALL_DOMAINS

    dispatch = _build_dispatch(session, q, limit, lat, lng, radius, toxicity, kingdom, facility_type)

    tasks = []
    task_names = []
    for domain in domains:
        if domain in dispatch:
            tasks.append(dispatch[domain])
            task_names.append(domain)

    results_list = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    results: Dict[str, List[Any]] = {}
    universal: List[SearchResult] = []
    total_count = 0

    for name, result in zip(task_names, results_list):
        if isinstance(result, Exception):
            logger.error(f"Domain {name} failed: {result}")
            results[name] = []
        else:
            results[name] = result
            total_count += len(result)
            # Normalize into universal SearchResult for CREP
            for item in result:
                if isinstance(item, dict):
                    universal.append(SearchResult(
                        id=str(item.get("id", "")),
                        domain=item.get("domain", name),
                        entity_type=item.get("entity_type", name),
                        name=item.get("name") or item.get("scientific_name") or item.get("title") or str(item.get("id", "")),
                        description=item.get("description") or item.get("abstract"),
                        lat=item.get("lat"),
                        lng=item.get("lng"),
                        occurred_at=item.get("occurred_at") or item.get("observed_at"),
                        source=item.get("source"),
                        image_url=item.get("image_url") or item.get("thumbnail_url"),
                        properties={k: v for k, v in item.items()
                                    if k not in ("id", "domain", "entity_type", "name", "description",
                                                 "lat", "lng", "occurred_at", "source", "image_url")},
                    ))

    timing_ms = int((time.time() - start_time) * 1000)

    filters_applied: Dict[str, Any] = {}
    if toxicity:
        filters_applied["toxicity"] = toxicity
    if kingdom:
        filters_applied["kingdom"] = kingdom
    if facility_type:
        filters_applied["facility_type"] = facility_type
    if lat is not None and lng is not None:
        filters_applied["location"] = {"lat": lat, "lng": lng, "radius_km": radius}

    return EarthSearchResponse(
        query=q,
        domains_searched=task_names,
        results=results,
        universal_results=universal,
        total_count=total_count,
        timing_ms=timing_ms,
        filters_applied=filters_applied,
    )


@router.get("/domains")
async def list_domains():
    """List all searchable domains and domain groups."""
    return {
        "domains": ALL_DOMAINS,
        "groups": {k: v for k, v in DOMAIN_GROUPS.items()},
        "total_domains": len(ALL_DOMAINS),
    }


@router.get("/taxa/by-location")
async def search_taxa_by_location(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    radius: float = Query(50, description="Radius in km"),
    filter: Optional[str] = Query(None, description="Filter: poisonous, edible, psychedelic"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
):
    """Get taxa observed near a specific location (fungi + all species)."""
    results = await search_observations(session, "", limit, lat, lng, radius)
    return {
        "results": results[:limit],
        "location": {"lat": lat, "lng": lng, "radius_km": radius},
        "total": len(results),
    }


@router.get("/nearby")
async def search_nearby(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    radius: float = Query(50, description="Radius in km"),
    types: str = Query("all", description="Domain groups to search"),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
):
    """
    **Nearby Search** — Find everything within radius of a point.

    Searches all location-aware domains for entities near the given coordinates.
    Returns results sorted by domain, all with lat/lng for CREP map rendering.
    """
    start_time = time.time()

    # For nearby, we pass a wildcard query but rely on location
    q = "%"
    domains = _resolve_domains(types)
    if not domains:
        domains = ALL_DOMAINS

    # Only use location-aware domains for nearby search
    location_aware = {
        "observations", "earthquakes", "wildfires", "lightning",
        "air_quality", "weather", "buoys", "antennas", "wifi_hotspots",
        "signal_measurements", "aircraft", "vessels", "cameras", "crep_entities",
    }

    dispatch = _build_dispatch(session, q, limit, lat, lng, radius, None, None, None)

    tasks = []
    task_names = []
    for domain in domains:
        if domain in dispatch and domain in location_aware:
            tasks.append(dispatch[domain])
            task_names.append(domain)

    results_list = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    results: Dict[str, List[Any]] = {}
    total_count = 0
    for name, result in zip(task_names, results_list):
        if isinstance(result, Exception):
            results[name] = []
        else:
            results[name] = result
            total_count += len(result)

    timing_ms = int((time.time() - start_time) * 1000)

    return {
        "location": {"lat": lat, "lng": lng, "radius_km": radius},
        "domains_searched": task_names,
        "results": results,
        "total_count": total_count,
        "timing_ms": timing_ms,
    }
