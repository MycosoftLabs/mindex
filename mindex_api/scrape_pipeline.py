"""
On-Demand Scrape Pipeline
============================
Implements the "local-first, scrape-on-miss" data access strategy:

1. User/agent searches for "volcano in Iceland"
2. Check local PostgreSQL → cache hit? Return instantly (<5ms)
3. Cache miss? Check Supabase → found? Return + copy to local (<50ms)
4. Not found anywhere? Live-scrape external APIs → return immediately
5. Async background task stores scraped data in local DB + Supabase
6. Next search for same thing → local hit (<5ms)

This ensures:
- First query: <2s (live scrape + return)
- Every subsequent query: <5ms (local cache)
- Myca, agents, search, CREP all get same low-latency access
- Data accumulates locally over time → less external API dependency
- NAS stores bulk/archival data for NLM training
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .cache import get_cache
from .supabase_client import get_supabase

logger = logging.getLogger(__name__)


class ScrapePipeline:
    """Orchestrates the local-first data access with live scrape fallback."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.cache = get_cache()
        self.supabase = get_supabase()

    async def search_with_fallback(
        self,
        domain: str,
        query: str,
        local_search_fn: Callable,
        live_scrape_fn: Optional[Callable] = None,
        cache_ttl: int = 120,
    ) -> List[dict]:
        """
        Multi-tier search with automatic data acquisition.

        1. Redis/LRU cache → instant
        2. Local PostgreSQL → fast
        3. Supabase cloud → medium
        4. Live scrape → slow but gets data, then caches it

        Args:
            domain: Data domain (e.g., "earthquakes", "species")
            query: Search query string
            local_search_fn: async fn(session, query) → List[dict]
            live_scrape_fn: optional sync/async fn(query) → List[dict]
            cache_ttl: Cache time-to-live in seconds
        """
        cache_key = f"pipeline:{domain}:{query}"
        start = time.time()

        # Tier 0+1: Cache (LRU + Redis)
        cached = await self.cache.get_json(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for {domain}:{query} ({(time.time()-start)*1000:.1f}ms)")
            return cached

        # Tier 2: Local PostgreSQL
        local_results = await local_search_fn(self.session, query)
        if local_results:
            # Cache locally
            await self.cache.set_json(cache_key, local_results, cache_ttl)
            logger.debug(f"Local DB hit for {domain}:{query} ({(time.time()-start)*1000:.1f}ms, {len(local_results)} results)")
            return local_results

        # Tier 3: Supabase cloud (if configured)
        if self.supabase.enabled:
            supa_results = await self.supabase.select(
                f"earth_entities",
                filters={"domain": domain},
                limit=20,
            )
            if supa_results:
                # Copy to local cache
                await self.cache.set_json(cache_key, supa_results, cache_ttl)
                # Async: store in local DB for future queries
                asyncio.create_task(self._store_locally(domain, supa_results))
                logger.debug(f"Supabase hit for {domain}:{query} ({(time.time()-start)*1000:.1f}ms)")
                return supa_results

        # Tier 4: Live scrape from external APIs
        if live_scrape_fn is not None:
            try:
                scraped = await self._execute_scrape(live_scrape_fn, query)
                if scraped:
                    # Return immediately
                    await self.cache.set_json(cache_key, scraped, cache_ttl)
                    # Async: store everywhere for future access
                    asyncio.create_task(self._store_and_sync(domain, query, scraped))
                    logger.info(f"Live scrape for {domain}:{query} ({(time.time()-start)*1000:.1f}ms, {len(scraped)} results)")
                    return scraped
            except Exception as e:
                logger.error(f"Live scrape failed for {domain}:{query}: {e}")

        return []

    async def _execute_scrape(self, scrape_fn: Callable, query: str) -> List[dict]:
        """Execute a scrape function (handles both sync and async)."""
        if asyncio.iscoroutinefunction(scrape_fn):
            return await scrape_fn(query)
        else:
            # Run sync scrape in thread pool to avoid blocking event loop
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, scrape_fn, query)

    async def _store_locally(self, domain: str, records: List[dict]):
        """Store records in local PostgreSQL (background task)."""
        try:
            for record in records:
                entity_type = record.get("entity_type", domain)
                lat = record.get("lat")
                lng = record.get("lng")

                if lat is not None and lng is not None:
                    await self.session.execute(text("""
                        INSERT INTO crep.unified_entities (id, entity_type, geometry, state,
                            observed_at, valid_from, source, confidence, s2_cell_id)
                        VALUES (:id, :type, ST_MakePoint(:lng, :lat)::geography,
                            :state::jsonb, COALESCE(:occurred_at, NOW()), NOW(),
                            :source, 0.8, 0)
                        ON CONFLICT (id, observed_at) DO NOTHING
                    """), {
                        "id": str(record.get("id", "")),
                        "type": entity_type,
                        "lng": lng, "lat": lat,
                        "state": json.dumps(record) if isinstance(record, dict) else "{}",
                        "occurred_at": record.get("occurred_at"),
                        "source": record.get("source", "scrape"),
                    })
                await self.session.commit()
        except Exception as e:
            logger.debug(f"Local store error: {e}")

    async def _store_and_sync(self, domain: str, query: str, records: List[dict]):
        """Store locally AND sync to Supabase (background task)."""
        # Store in local DB
        await self._store_locally(domain, records)

        # Mark as scraped so we don't re-scrape
        await self.cache.mark_scraped(domain, query)

        # Sync to Supabase
        if self.supabase.enabled:
            await self.supabase.sync_earth_entities(records)


# Import here to avoid circular
import json


# ============================================================================
# LIVE SCRAPE FUNCTIONS — Called when local data is missing
# ============================================================================

def scrape_earthquakes_live(query: str) -> List[dict]:
    """Live-scrape USGS earthquakes matching query."""
    from mindex_etl.sources.usgs_earthquakes import fetch_recent_earthquakes
    results = fetch_recent_earthquakes(hours=24, min_magnitude=2.0)
    # Filter by query
    q = query.lower()
    return [
        r for r in results
        if q in (r.get("place_name", "") or "").lower()
        or q in str(r.get("magnitude", ""))
    ][:20]


def scrape_species_live(query: str) -> List[dict]:
    """Live-scrape GBIF for species matching query."""
    import httpx
    try:
        resp = httpx.get(
            "https://api.gbif.org/v1/species/search",
            params={"q": query, "limit": 20},
            timeout=10,
        )
        resp.raise_for_status()
        results = []
        for record in resp.json().get("results", []):
            results.append({
                "id": f"gbif_{record.get('key', '')}",
                "domain": "species",
                "entity_type": record.get("kingdom", "organism"),
                "name": record.get("canonicalName") or record.get("scientificName"),
                "scientific_name": record.get("scientificName"),
                "common_name": record.get("vernacularName"),
                "kingdom": record.get("kingdom"),
                "source": "gbif",
                "properties": {
                    "gbif_key": record.get("key"),
                    "rank": record.get("rank"),
                    "phylum": record.get("phylum"),
                    "class": record.get("class"),
                    "family": record.get("family"),
                },
            })
        return results
    except Exception:
        return []


def scrape_aircraft_live(query: str) -> List[dict]:
    """Live-scrape OpenSky for aircraft."""
    from mindex_etl.sources.opensky import iter_aircraft
    results = []
    for ac in iter_aircraft():
        callsign = (ac.get("callsign") or "").lower()
        icao = (ac.get("icao24") or "").lower()
        if query.lower() in callsign or query.lower() in icao:
            ac["id"] = f"ac_{ac.get('icao24', '')}"
            ac["domain"] = "transport"
            ac["entity_type"] = "aircraft"
            ac["name"] = ac.get("callsign") or ac.get("icao24")
            results.append(ac)
            if len(results) >= 20:
                break
    return results


def scrape_satellites_live(query: str) -> List[dict]:
    """Live-scrape CelesTrak for satellites."""
    from mindex_etl.sources.celestrak import iter_satellites
    q = query.lower()
    results = []
    for sat in iter_satellites(groups=["active"]):
        name = (sat.get("name") or "").lower()
        if q in name or q in str(sat.get("norad_id", "")):
            sat["id"] = f"sat_{sat.get('norad_id', '')}"
            sat["domain"] = "space"
            sat["entity_type"] = "satellite"
            results.append(sat)
            if len(results) >= 20:
                break
    return results


def scrape_air_quality_live(query: str) -> List[dict]:
    """Live-scrape OpenAQ for air quality."""
    import httpx
    try:
        resp = httpx.get(
            "https://api.openaq.org/v2/measurements",
            params={"city": query, "limit": 20, "order_by": "datetime", "sort": "desc"},
            timeout=10,
        )
        resp.raise_for_status()
        results = []
        for r in resp.json().get("results", []):
            coords = r.get("coordinates", {})
            results.append({
                "id": f"aq_{r.get('locationId', '')}_{r.get('parameter', '')}",
                "domain": "atmosphere",
                "entity_type": "air_quality",
                "name": f"{r.get('location', 'Station')} — {r.get('parameter', '')}: {r.get('value', '')}",
                "lat": coords.get("latitude"),
                "lng": coords.get("longitude"),
                "occurred_at": r.get("date", {}).get("utc"),
                "source": "openaq",
            })
        return results
    except Exception:
        return []


# Map of domains to their live scrape functions
LIVE_SCRAPERS: Dict[str, Callable] = {
    "earthquakes": scrape_earthquakes_live,
    "species": scrape_species_live,
    "aircraft": scrape_aircraft_live,
    "satellites": scrape_satellites_live,
    "air_quality": scrape_air_quality_live,
}
