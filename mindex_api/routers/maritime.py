"""Maritime Data API Router — TAC-O Maritime Integration.

Real Postgres-backed CRUD/query endpoints for acoustic signatures,
ocean environments, and magnetic baselines.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/maritime", tags=["maritime"])


class AcousticSignatureCreate(BaseModel):
    name: str
    category: str
    subcategory: Optional[str] = None
    frequency_range_low: Optional[float] = None
    frequency_range_high: Optional[float] = None
    spectral_energy: Optional[Dict[str, Any]] = None
    narrowband_peaks: Optional[List[Any]] = None
    broadband_level: Optional[float] = None
    modulation_rate: Optional[float] = None
    source: Optional[str] = None
    confidence: float = 0.0


class OceanEnvironmentCreate(BaseModel):
    latitude: float
    longitude: float
    depth_m: Optional[float] = None
    sound_speed: Optional[float] = None
    temperature_c: Optional[float] = None
    salinity_psu: Optional[float] = None
    sea_state: Optional[int] = None
    current_speed: Optional[float] = None
    current_direction: Optional[float] = None
    bottom_depth: Optional[float] = None
    bottom_type: Optional[str] = None
    sound_speed_profile: Optional[List[Any]] = None
    ambient_noise_spectrum: Optional[List[Any]] = None
    observed_at: str
    source: Optional[str] = None


class MagneticBaselineCreate(BaseModel):
    latitude: float
    longitude: float
    bx: float
    by: float
    bz: float
    total_field: float
    inclination: float
    declination: float
    survey_date: Optional[str] = None
    source: Optional[str] = None


@router.get("/acoustic-signatures")
async def list_acoustic_signatures(
    category: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
    """List cataloged acoustic signatures with optional category filter."""
    params: Dict[str, Any] = {"limit": limit, "offset": offset, "category": category}
    result = await db.execute(
        text(
            """
            SELECT signature_id, name, category, subcategory, frequency_range_low,
                   frequency_range_high, spectral_energy, narrowband_peaks,
                   broadband_level, modulation_rate, source, confidence,
                   created_at, updated_at
            FROM acoustic_signatures
            WHERE (:category IS NULL OR category = :category)
            ORDER BY updated_at DESC, created_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    rows = result.mappings().all()
    return {"signatures": [dict(row) for row in rows], "total": len(rows), "limit": limit, "offset": offset}


@router.get("/acoustic-signatures/{signature_id}")
async def get_acoustic_signature(signature_id: UUID, db: AsyncSession = Depends(get_db_session)):
    """Get a specific acoustic signature by ID."""
    result = await db.execute(
        text(
            """
            SELECT signature_id, name, category, subcategory, frequency_range_low,
                   frequency_range_high, spectral_energy, narrowband_peaks,
                   broadband_level, modulation_rate, source, confidence,
                   created_at, updated_at
            FROM acoustic_signatures
            WHERE signature_id = :signature_id
            LIMIT 1
            """
        ),
        {"signature_id": signature_id},
    )
    row = result.mappings().first()
    return {"signature": dict(row)} if row else {"signature_id": str(signature_id), "status": "not_found"}


@router.post("/acoustic-signatures")
async def create_acoustic_signature(sig: AcousticSignatureCreate, db: AsyncSession = Depends(get_db_session)):
    """Create a new acoustic signature entry."""
    result = await db.execute(
        text(
            """
            INSERT INTO acoustic_signatures (
                name, category, subcategory, frequency_range_low, frequency_range_high,
                spectral_energy, narrowband_peaks, broadband_level, modulation_rate,
                source, confidence
            ) VALUES (
                :name, :category, :subcategory, :frequency_range_low, :frequency_range_high,
                CAST(:spectral_energy AS jsonb), CAST(:narrowband_peaks AS jsonb), :broadband_level,
                :modulation_rate, :source, :confidence
            )
            RETURNING signature_id
            """
        ),
        {
            **sig.model_dump(),
            "spectral_energy": sig.spectral_energy and __import__("json").dumps(sig.spectral_energy),
            "narrowband_peaks": sig.narrowband_peaks and __import__("json").dumps(sig.narrowband_peaks),
        },
    )
    await db.commit()
    return {"status": "created", "signature_id": str(result.scalar_one())}


@router.get("/ocean-environments")
async def list_ocean_environments(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    radius_nm: Optional[float] = None,
    limit: int = Query(default=50, le=500),
    db: AsyncSession = Depends(get_db_session),
):
    """Query ocean environment observations, optionally by location."""
    params: Dict[str, Any] = {"lat": lat, "lon": lon, "radius_m": (radius_nm or 0) * 1852, "limit": limit}
    query = """
        SELECT observation_id, ST_Y(location::geometry) AS latitude, ST_X(location::geometry) AS longitude,
               depth_m, sound_speed, temperature_c, salinity_psu, sea_state,
               current_speed, current_direction, bottom_depth, bottom_type,
               sound_speed_profile, ambient_noise_spectrum, observed_at, source, created_at
        FROM ocean_environments
    """
    if lat is not None and lon is not None and radius_nm is not None:
        query += """
        WHERE ST_DWithin(
            location,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
            :radius_m
        )
        """
    query += " ORDER BY observed_at DESC LIMIT :limit"
    result = await db.execute(text(query), params)
    rows = result.mappings().all()
    return {"environments": [dict(row) for row in rows], "total": len(rows), "limit": limit}


@router.post("/ocean-environments")
async def create_ocean_environment(env: OceanEnvironmentCreate, db: AsyncSession = Depends(get_db_session)):
    """Store an ocean environment observation."""
    result = await db.execute(
        text(
            """
            INSERT INTO ocean_environments (
                location, depth_m, sound_speed, temperature_c, salinity_psu, sea_state,
                current_speed, current_direction, bottom_depth, bottom_type,
                sound_speed_profile, ambient_noise_spectrum, observed_at, source
            ) VALUES (
                ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                :depth_m, :sound_speed, :temperature_c, :salinity_psu, :sea_state,
                :current_speed, :current_direction, :bottom_depth, :bottom_type,
                CAST(:sound_speed_profile AS jsonb), CAST(:ambient_noise_spectrum AS jsonb), :observed_at, :source
            )
            RETURNING observation_id
            """
        ),
        {
            **env.model_dump(),
            "sound_speed_profile": env.sound_speed_profile and __import__("json").dumps(env.sound_speed_profile),
            "ambient_noise_spectrum": env.ambient_noise_spectrum and __import__("json").dumps(env.ambient_noise_spectrum),
        },
    )
    await db.commit()
    return {"status": "created", "observation_id": str(result.scalar_one())}


@router.get("/magnetic-baselines")
async def list_magnetic_baselines(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    radius_nm: Optional[float] = None,
    limit: int = Query(default=50, le=500),
    db: AsyncSession = Depends(get_db_session),
):
    """Query magnetic field baselines by location."""
    params: Dict[str, Any] = {"lat": lat, "lon": lon, "radius_m": (radius_nm or 0) * 1852, "limit": limit}
    query = """
        SELECT ST_Y(location::geometry) AS latitude, ST_X(location::geometry) AS longitude,
               bx, by, bz, total_field, inclination, declination, survey_date, source, created_at
        FROM magnetic_baselines
    """
    if lat is not None and lon is not None and radius_nm is not None:
        query += """
        WHERE ST_DWithin(
            location,
            ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
            :radius_m
        )
        """
    query += " ORDER BY survey_date DESC NULLS LAST, created_at DESC LIMIT :limit"
    result = await db.execute(text(query), params)
    rows = result.mappings().all()
    return {"baselines": [dict(row) for row in rows], "total": len(rows), "limit": limit}


@router.post("/magnetic-baselines")
async def create_magnetic_baseline(baseline: MagneticBaselineCreate, db: AsyncSession = Depends(get_db_session)):
    """Store a magnetic field baseline measurement."""
    await db.execute(
        text(
            """
            INSERT INTO magnetic_baselines (
                location, bx, by, bz, total_field, inclination, declination, survey_date, source
            ) VALUES (
                ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                :bx, :by, :bz, :total_field, :inclination, :declination, :survey_date, :source
            )
            """
        ),
        baseline.model_dump(),
    )
    await db.commit()
    return {"status": "created", "baseline": baseline.model_dump()}
