"""Maritime Worldview API — TAC-O Maritime Integration

Maritime-specific Worldview endpoints providing environmental,
threat, sensor health, and decision aid data for TAC-O operators.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import CallerIdentity, require_worldview_key
from ...dependencies import get_db_session
from .response_envelope import wrap_governed_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/worldview/maritime", tags=["worldview", "maritime"])


@router.get("/acoustic-environment")
async def get_acoustic_environment(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    radius_nm: float = Query(default=10.0, description="Search radius in nautical miles"),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
):
    """Sound speed profiles, ambient noise, and environmental conditions."""
    result = await db.execute(
        text(
            """
            SELECT observation_id, ST_Y(location::geometry) AS latitude, ST_X(location::geometry) AS longitude,
                   sound_speed, temperature_c, salinity_psu, sea_state, current_speed, current_direction,
                   sound_speed_profile, ambient_noise_spectrum, observed_at, source
            FROM ocean_environments
            WHERE ST_DWithin(
                location,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                :radius_m
            )
            ORDER BY observed_at DESC
            LIMIT 25
            """
        ),
        {"lat": lat, "lon": lon, "radius_m": radius_nm * 1852},
    )
    rows = result.mappings().all()
    data = {
        "location": {"lat": lat, "lon": lon},
        "radius_nm": radius_nm,
        "acoustic_environment": [dict(row) for row in rows],
        "source": "ocean_environments",
    }
    return await wrap_governed_response(
        data=data,
        count=len(rows),
        caller=caller,
        source_domains=["maritime", "buoys"],
        region={"lat": lat, "lon": lon, "radius_nm": radius_nm},
    )


@router.get("/threat-assessment")
async def get_threat_assessment(
    sector: Optional[str] = Query(default=None, description="Operational sector filter"),
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
):
    """Current FUSARIUM Maritime threat assessments."""
    result = await db.execute(
        text(
            """
            SELECT assessment_id, assessment_type, classification, recommendation,
                   sonar_performance, urgency, avani_ecological_check, assessed_at
            FROM taco_assessments
            WHERE (:sector IS NULL OR assessment_type = :sector)
            ORDER BY assessed_at DESC
            LIMIT 50
            """
        ),
        {"sector": sector},
    )
    rows = result.mappings().all()
    return await wrap_governed_response(
        data={"sector": sector, "threats": [dict(row) for row in rows], "total": len(rows), "source": "taco_assessments"},
        count=len(rows),
        caller=caller,
        source_domains=["maritime", "fusarium_tracks"],
    )


@router.get("/sensor-health")
async def get_sensor_health(
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
):
    """Maritime sensor network status and health metrics."""
    result = await db.execute(
        text(
            """
            SELECT DISTINCT ON (sensor_id)
                   sensor_id, sensor_type, observed_at AS last_seen, confidence, anomaly_score,
                   ST_Y(location::geometry) AS latitude, ST_X(location::geometry) AS longitude
            FROM taco_observations
            ORDER BY sensor_id, observed_at DESC
            """
        )
    )
    rows = result.mappings().all()
    sensors = [dict(row) for row in rows]
    return await wrap_governed_response(
        data={"sensors": sensors, "total": len(sensors), "network_status": "online" if sensors else "degraded", "source": "taco_observations"},
        count=len(sensors),
        caller=caller,
        source_domains=["maritime", "sensor_health"],
    )


@router.get("/decision-aid")
async def get_decision_aid(
    caller: CallerIdentity = Depends(require_worldview_key),
    db: AsyncSession = Depends(get_db_session),
):
    """MYCA tactical recommendations for current operational picture."""
    result = await db.execute(
        text(
            """
            SELECT assessment_id, recommendation, urgency, assessed_at
            FROM taco_assessments
            WHERE recommendation IS NOT NULL
            ORDER BY assessed_at DESC
            LIMIT 25
            """
        )
    )
    rows = result.mappings().all()
    return await wrap_governed_response(
        data={"recommendations": [dict(row) for row in rows], "total": len(rows), "source": "taco_assessments"},
        count=len(rows),
        caller=caller,
        source_domains=["maritime", "fusarium_correlations"],
    )
