"""Maritime Data Sync Job — TAC-O Maritime Integration

Scheduled sync for NOAA, HYCOM, and hydrophone feeds.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

NDBC_STATIONS_PACIFIC = [
    "46025", "46026", "46027", "46028", "46029",
    "46042", "46047", "46053", "46054", "46069",
]
NDBC_STATIONS_ATLANTIC = [
    "41001", "41002", "41004", "41008", "41009",
    "41013", "41025", "41040", "41043", "41047",
]


async def sync_ocean_environments(db_pool=None, stations: Optional[List[str]] = None):
    """Sync ocean environment data from NOAA NDBC buoy stations."""
    from mindex_etl.sources.noaa_ocean import NOAAOceanSource
    source = NOAAOceanSource(db_pool=db_pool)
    target_stations = stations or NDBC_STATIONS_PACIFIC + NDBC_STATIONS_ATLANTIC
    ingested = 0
    try:
        for station_id in target_stations:
            obs = await source.fetch_station_observations(station_id)
            if obs:
                obs_id = await source.ingest_to_db(obs)
                if obs_id:
                    ingested += 1
    finally:
        await source.close()
    logger.info("Ocean env sync: %d ingested", ingested)
    return ingested


async def sync_hydrophone_data(db_pool=None):
    """Sync acoustic training data from NOAA PMEL hydrophone network."""
    from mindex_etl.sources.noaa_hydrophone import NOAAHydrophoneSource
    source = NOAAHydrophoneSource(db_pool=db_pool)
    ingested = 0
    try:
        stations = await source.fetch_station_catalog()
        for station in stations:
            spectrograms = await source.fetch_spectrograms(
                station.get("id", ""), "2025-01-01",
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            )
            if spectrograms:
                for spec in spectrograms:
                    sig_id = await source.ingest_acoustic_signature(spec)
                    if sig_id:
                        ingested += 1
    finally:
        await source.close()
    logger.info("Hydrophone sync: %d ingested", ingested)
    return ingested


async def run_full_maritime_sync(db_pool=None):
    """Run all maritime data sync jobs."""
    logger.info("Starting maritime sync at %s", datetime.now(timezone.utc))
    env_count = await sync_ocean_environments(db_pool=db_pool)
    hydro_count = await sync_hydrophone_data(db_pool=db_pool)
    total = env_count + hydro_count
    logger.info("Maritime sync complete: %d total", total)
    return {"ocean_environments": env_count, "hydrophone": hydro_count, "total": total}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_full_maritime_sync())
