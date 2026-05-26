"""
Civic viewport intelligence batch sync (May 24, 2026)
=====================================================
ETL all civic/government viewport sources into MINDEX civic.* tables.
Run daily or weekly — the Earth Simulator read path serves from MINDEX only.

Usage:
    python -m mindex_etl.jobs.civic_viewport_sync
    python -m mindex_etl.jobs.civic_viewport_sync --max-seeds 10
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from mindex_api.db import async_session_scope
from mindex_api.routers.civic_unified import refresh_viewport_intel_for_bounds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("civic_viewport_sync")

# US state capitals + major metros — bbox ~3° for state-level civic coverage
VIEWPORT_SEEDS: list[dict[str, Any]] = [
    {"label": "United States", "lat": 39.8283, "lng": -98.5795, "zoom": 4, "delta": 12.0},
    {"label": "California", "lat": 36.7783, "lng": -119.4179, "zoom": 6, "delta": 3.0},
    {"label": "Texas", "lat": 31.9686, "lng": -99.9018, "zoom": 6, "delta": 4.0},
    {"label": "Florida", "lat": 27.6648, "lng": -81.5158, "zoom": 6, "delta": 3.0},
    {"label": "New York", "lat": 42.1657, "lng": -74.9481, "zoom": 6, "delta": 3.0},
    {"label": "Washington DC", "lat": 38.9072, "lng": -77.0369, "zoom": 10, "delta": 0.35},
    {"label": "San Diego CA", "lat": 32.7157, "lng": -117.1611, "zoom": 10, "delta": 0.35},
    {"label": "Los Angeles CA", "lat": 34.0522, "lng": -118.2437, "zoom": 10, "delta": 0.4},
    {"label": "San Francisco CA", "lat": 37.7749, "lng": -122.4194, "zoom": 10, "delta": 0.35},
    {"label": "Seattle WA", "lat": 47.6062, "lng": -122.3321, "zoom": 10, "delta": 0.35},
    {"label": "Chicago IL", "lat": 41.8781, "lng": -87.6298, "zoom": 10, "delta": 0.4},
    {"label": "Houston TX", "lat": 29.7604, "lng": -95.3698, "zoom": 10, "delta": 0.4},
    {"label": "Miami FL", "lat": 25.7617, "lng": -80.1918, "zoom": 10, "delta": 0.35},
    {"label": "Denver CO", "lat": 39.7392, "lng": -104.9903, "zoom": 10, "delta": 0.35},
    {"label": "Phoenix AZ", "lat": 33.4484, "lng": -112.0740, "zoom": 10, "delta": 0.4},
    {"label": "Atlanta GA", "lat": 33.7490, "lng": -84.3880, "zoom": 10, "delta": 0.35},
    {"label": "Boston MA", "lat": 42.3601, "lng": -71.0589, "zoom": 10, "delta": 0.35},
    {"label": "Philadelphia PA", "lat": 39.9526, "lng": -75.1652, "zoom": 10, "delta": 0.35},
]


def _bbox(lat: float, lng: float, delta: float) -> dict[str, float]:
    return {
        "north": lat + delta,
        "south": lat - delta,
        "east": lng + delta,
        "west": lng - delta,
    }


async def _run_async(max_seeds: int | None = None) -> int:
    seeds = VIEWPORT_SEEDS if max_seeds is None else VIEWPORT_SEEDS[:max_seeds]
    processed = 0
    async with async_session_scope() as db:
        for seed in seeds:
            bbox = _bbox(float(seed["lat"]), float(seed["lng"]), float(seed["delta"]))
            label = str(seed.get("label") or "viewport")
            zoom = float(seed.get("zoom") or 8)
            try:
                response = await refresh_viewport_intel_for_bounds(
                    db,
                    north=bbox["north"],
                    south=bbox["south"],
                    east=bbox["east"],
                    west=bbox["west"],
                    zoom=zoom,
                )
                officials = len(response.representatives or [])
                elections = len(response.elections or [])
                facilities = len(response.facilities or [])
                logger.info(
                    "%s — officials=%s elections=%s facilities=%s lod=%s",
                    label,
                    officials,
                    elections,
                    facilities,
                    response.lod,
                )
                processed += 1
            except Exception:
                logger.exception("Failed civic viewport sync for %s", label)
    return processed


def sync_civic_viewport_intel(max_seeds: int | None = None, **kwargs: Any) -> int:
    """ETL job entrypoint for run_all registry."""
    _ = kwargs
    return asyncio.run(_run_async(max_seeds=max_seeds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-sync civic viewport intelligence into MINDEX")
    parser.add_argument("--max-seeds", type=int, default=None, help="Limit number of viewport seeds")
    args = parser.parse_args()
    count = sync_civic_viewport_intel(max_seeds=args.max_seeds)
    logger.info("Civic viewport sync complete — %s seeds processed", count)


if __name__ == "__main__":
    main()
