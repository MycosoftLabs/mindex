"""Navy Oceanographic Model ETL Source — TAC-O Maritime Integration

Ingests public ocean model outputs from HYCOM and GOFS.
Public data: https://www.hycom.org/data/glby0pt08
"""

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

HYCOM_BASE = "https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0"


class NavyOceanographicSource:
    """Ingest ocean model data from HYCOM and related Navy models."""

    def __init__(self, db_pool=None):
        self.db_pool = db_pool
        self.client = httpx.AsyncClient(timeout=60.0)

    async def fetch_hycom_profile(self, lat: float, lon: float, depth_range=(0, 1000)):
        """Fetch temperature/salinity profile from HYCOM."""
        logger.info("HYCOM profile fetch at (%.4f, %.4f)", lat, lon)
        return None

    async def fetch_current_forecast(self, lat: float, lon: float):
        """Fetch ocean current forecast from HYCOM."""
        logger.info("HYCOM current forecast at (%.4f, %.4f)", lat, lon)
        return None

    async def ingest_profile_to_db(self, profile: Dict[str, Any]) -> Optional[str]:
        """Store ocean model profile in MINDEX."""
        if not self.db_pool or not profile:
            return None
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO ocean_environments
                        (location, sound_speed_profile, temperature_c, salinity_psu,
                         current_speed, current_direction, observed_at, source)
                    VALUES (ST_SetSRID(ST_MakePoint($1, $2), 4326),
                            $3, $4, $5, $6, $7, $8, $9)
                    RETURNING observation_id""",
                    profile.get("longitude", 0), profile.get("latitude", 0),
                    profile.get("sound_speed_profile"), profile.get("temperature_c"),
                    profile.get("salinity_psu"), profile.get("current_speed"),
                    profile.get("current_direction"), profile.get("observed_at"),
                    profile.get("source", "hycom"),
                )
                return str(row["observation_id"]) if row else None
        except Exception as e:
            logger.error("DB insert failed: %s", e)
            return None

    async def close(self):
        await self.client.aclose()
