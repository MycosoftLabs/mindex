"""NOAA Ocean Data ETL Source — TAC-O Maritime Integration

Ingests NOAA National Data Buoy Center (NDBC) oceanographic data
into the ocean_environments table for NLM WorldState context.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

NDBC_REALTIME_URL = "https://www.ndbc.noaa.gov/data/realtime2"


class NOAAOceanSource:
    """Ingest oceanographic data from NOAA NDBC buoy network."""

    def __init__(self, db_pool=None):
        self.db_pool = db_pool
        self.client = httpx.AsyncClient(timeout=30.0)

    async def fetch_station_observations(self, station_id: str) -> Optional[Dict[str, Any]]:
        """Fetch latest observation from an NDBC buoy station."""
        url = f"{NDBC_REALTIME_URL}/{station_id}.txt"
        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            return self._parse_ndbc_observation(resp.text, station_id)
        except Exception as e:
            logger.error("Failed to fetch NDBC station %s: %s", station_id, e)
            return None

    def _parse_ndbc_observation(self, raw_text: str, station_id: str) -> Optional[Dict[str, Any]]:
        """Parse NDBC standard meteorological data format."""
        lines = raw_text.strip().split("\n")
        if len(lines) < 3:
            return None
        headers = lines[0].replace("#", "").split()
        data_line = lines[2].split()
        if len(data_line) < len(headers):
            return None
        obs = dict(zip(headers, data_line))
        try:
            return {
                "station_id": station_id,
                "temperature_c": self._safe_float(obs.get("WTMP")),
                "salinity_psu": None,
                "sea_state": self._safe_int(obs.get("WVHT")),
                "current_speed": self._safe_float(obs.get("SPD")),
                "current_direction": self._safe_float(obs.get("DIR")),
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "source": f"ndbc:{station_id}",
            }
        except (ValueError, KeyError) as e:
            logger.warning("Parse error for station %s: %s", station_id, e)
            return None

    async def ingest_to_db(self, observation: Dict[str, Any]) -> Optional[str]:
        """Store ocean environment observation in MINDEX."""
        if not self.db_pool or not observation:
            return None
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO ocean_environments
                        (location, temperature_c, salinity_psu, sea_state,
                         current_speed, current_direction, observed_at, source)
                    VALUES (ST_SetSRID(ST_MakePoint($1, $2), 4326),
                            $3, $4, $5, $6, $7, $8, $9)
                    RETURNING observation_id""",
                    observation.get("longitude", 0), observation.get("latitude", 0),
                    observation.get("temperature_c"), observation.get("salinity_psu"),
                    observation.get("sea_state"), observation.get("current_speed"),
                    observation.get("current_direction"), observation.get("observed_at"),
                    observation.get("source"),
                )
                return str(row["observation_id"]) if row else None
        except Exception as e:
            logger.error("DB insert failed: %s", e)
            return None

    @staticmethod
    def _safe_float(val):
        if val is None or val in ("MM", "999", "99.0", "999.0"):
            return None
        try:
            return float(val)
        except ValueError:
            return None

    @staticmethod
    def _safe_int(val):
        if val is None or val in ("MM", "99"):
            return None
        try:
            return int(float(val))
        except ValueError:
            return None

    async def close(self):
        await self.client.aclose()
