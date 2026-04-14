#!/usr/bin/env python3
"""
MINDEX iNaturalist ETL — Bulk load biodiversity observations

iNaturalist has 305M+ observations of 560K+ species. This loader fetches
recent observations in chunks via the iNaturalist API v1 and loads them
into MINDEX species.sightings table.

Strategy:
  1. Load newest observations first (most relevant for CREP)
  2. Chunk by date range (1 day at a time) to stay within API limits
  3. Filter by iconic_taxon for all categories: birds, mammals, reptiles,
     amphibians, fish, mollusks, arachnids, insects, plants, fungi, protozoans
  4. Store with full taxonomy, coordinates, photos, and observer info
  5. All data persists in MINDEX PostGIS on NAS for offline CREP access

API limits: 200 results per page, 10,000 per search
Rate limit: 60 requests/minute (authenticated), 30/minute (anonymous)

Usage:
  python etl_inaturalist.py --days 30        # Last 30 days
  python etl_inaturalist.py --days 365       # Last year
  python etl_inaturalist.py --taxon Fungi    # Only fungi
  python etl_inaturalist.py --quality research # Only research grade
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import httpx
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("inat-etl")

DB_DSN = "postgresql://mycosoft:mycosoft_mindex_2026@192.168.0.189:5432/mindex"

INAT_API = "https://api.inaturalist.org/v1"
INAT_TOKEN = os.environ.get("INAT_API_TOKEN",
    "eyJhbGciOiJIUzUxMiJ9.eyJ1c2VyX2lkIjoxMDAxOTc2OSwiZXhwIjoxNzY1OTE1MDY2fQ.JXV3lLOyuuXeItfNUagixJCtKN3SI20_em1sl2gKFFDppHBNJXy79x6I6jJbiPG1a6n_-cj1JgysSmuKlbDKVg"
)

# iNaturalist iconic taxa
ICONIC_TAXA = [
    "Fungi",        # Including lichens
    "Plantae",      # Plants
    "Aves",         # Birds
    "Mammalia",     # Mammals
    "Reptilia",     # Reptiles
    "Amphibia",     # Amphibians
    "Actinopterygii", # Ray-finned fishes
    "Mollusca",     # Mollusks
    "Arachnida",    # Arachnids
    "Insecta",      # Insects
    "Protozoa",     # Protozoans
]

# Rate limiter
_last_request_time = 0
RATE_LIMIT_DELAY = 1.1  # seconds between requests (authenticated: 60/min)


async def rate_limited_get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """GET with rate limiting and auth."""
    global _last_request_time
    now = time.time()
    wait = RATE_LIMIT_DELAY - (now - _last_request_time)
    if wait > 0:
        await asyncio.sleep(wait)

    headers = {}
    if INAT_TOKEN:
        headers["Authorization"] = f"Bearer {INAT_TOKEN}"

    resp = await client.get(url, params=params, headers=headers, timeout=30)
    _last_request_time = time.time()

    if resp.status_code == 429:
        log.warning("  Rate limited, waiting 60s...")
        await asyncio.sleep(60)
        return await rate_limited_get(client, url, params)

    if resp.status_code != 200:
        log.warning(f"  HTTP {resp.status_code}")
        return {"results": [], "total_results": 0}

    return resp.json()


async def load_observations(conn: asyncpg.Connection, days: int = 30,
                            taxon_filter: str = "", quality: str = "research"):
    """Load iNaturalist observations for the given time range."""
    log.info(f"Loading iNaturalist observations (last {days} days, quality={quality})")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    total_inserted = 0

    async with httpx.AsyncClient() as client:
        # Process one day at a time, newest first
        current_date = end_date
        while current_date > start_date:
            day_start = (current_date - timedelta(days=1)).strftime("%Y-%m-%d")
            day_end = current_date.strftime("%Y-%m-%d")

            # Fetch observations for this day
            page = 1
            day_count = 0

            while True:
                params = {
                    "d1": day_start,
                    "d2": day_end,
                    "quality_grade": quality,
                    "geo": "true",  # Only geotagged
                    "per_page": 200,
                    "page": page,
                    "order": "desc",
                    "order_by": "observed_on",
                }
                if taxon_filter:
                    params["iconic_taxa"] = taxon_filter

                data = await rate_limited_get(client, f"{INAT_API}/observations", params)
                results = data.get("results", [])
                total = data.get("total_results", 0)

                if not results:
                    break

                # Insert batch
                batch = []
                for obs in results:
                    try:
                        lat = obs.get("geojson", {}).get("coordinates", [None, None])[1]
                        lng = obs.get("geojson", {}).get("coordinates", [None, None])[0]
                        if not lat or not lng:
                            continue

                        taxon = obs.get("taxon", {})
                        species_name = taxon.get("name", "Unknown")
                        common_name = taxon.get("preferred_common_name", "")
                        kingdom = taxon.get("iconic_taxon_name", "")
                        taxon_id = taxon.get("id")

                        observed_at = obs.get("observed_on_string") or obs.get("observed_on") or day_start
                        photos = [p.get("url", "").replace("square", "medium") for p in obs.get("photos", [])[:3]]

                        source_id = f"inat-{obs.get('id', 0)}"

                        batch.append((
                            source_id,
                            species_name,
                            common_name,
                            kingdom,
                            lng, lat,
                            observed_at,
                            obs.get("quality_grade", quality),
                            json.dumps({
                                "taxon_id": taxon_id,
                                "kingdom": kingdom,
                                "species": species_name,
                                "common_name": common_name,
                                "observer": obs.get("user", {}).get("login"),
                                "photos": photos,
                                "place_guess": obs.get("place_guess"),
                                "num_identification_agreements": obs.get("num_identification_agreements", 0),
                                "uri": obs.get("uri"),
                            }),
                        ))
                    except Exception:
                        pass

                if batch:
                    try:
                        await conn.executemany("""
                            INSERT INTO species.sightings (source, source_id, scientific_name,
                                common_name, kingdom, location, observed_at, quality_grade, properties)
                            VALUES ('inaturalist', $1, $2, $3, $4,
                                ST_SetSRID(ST_MakePoint($5, $6), 4326)::geography,
                                $7::text::timestamptz, $8, $9::jsonb)
                            ON CONFLICT DO NOTHING
                        """, batch)
                        day_count += len(batch)
                    except Exception as e:
                        if day_count == 0:
                            log.error(f"  Insert error: {e}")
                            # Table might not exist with these exact columns — try creating
                            try:
                                await conn.execute("""
                                    CREATE TABLE IF NOT EXISTS species.sightings (
                                        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                                        source VARCHAR(50) NOT NULL DEFAULT 'inaturalist',
                                        source_id VARCHAR(100),
                                        scientific_name TEXT,
                                        common_name TEXT,
                                        kingdom VARCHAR(100),
                                        location GEOGRAPHY(POINT, 4326) NOT NULL,
                                        observed_at TIMESTAMPTZ,
                                        quality_grade VARCHAR(50),
                                        organism_id UUID,
                                        properties JSONB NOT NULL DEFAULT '{}'::jsonb,
                                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                        UNIQUE (source, source_id)
                                    );
                                    CREATE INDEX IF NOT EXISTS idx_sighting_geo ON species.sightings USING GIST (location);
                                    CREATE INDEX IF NOT EXISTS idx_sighting_time ON species.sightings (observed_at DESC);
                                    CREATE INDEX IF NOT EXISTS idx_sighting_kingdom ON species.sightings (kingdom);
                                """)
                                log.info("  Created species.sightings table")
                                # Retry insert
                                await conn.executemany("""
                                    INSERT INTO species.sightings (source, source_id, scientific_name,
                                        common_name, kingdom, location, observed_at, quality_grade, properties)
                                    VALUES ('inaturalist', $1, $2, $3, $4,
                                        ST_SetSRID(ST_MakePoint($5, $6), 4326)::geography,
                                        $7::text::timestamptz, $8, $9::jsonb)
                                    ON CONFLICT DO NOTHING
                                """, batch)
                                day_count += len(batch)
                            except Exception as e2:
                                log.error(f"  Retry failed: {e2}")

                # Check if we've gotten all results for this day
                if page * 200 >= min(total, 10000) or len(results) < 200:
                    break
                page += 1

            if day_count > 0:
                total_inserted += day_count
                log.info(f"  {day_start}: {day_count} observations (total: {total_inserted:,})")

            current_date -= timedelta(days=1)

    return total_inserted


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="MINDEX iNaturalist ETL")
    parser.add_argument("--days", type=int, default=30, help="Days of data to load (default: 30)")
    parser.add_argument("--taxon", default="", help="Filter by iconic taxon (Fungi, Aves, Mammalia, etc.)")
    parser.add_argument("--quality", default="research", help="Quality grade filter (research, needs_id, casual)")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("MINDEX iNaturalist ETL — Biodiversity Data Loader")
    log.info(f"  Days: {args.days}, Taxon: {args.taxon or 'all'}, Quality: {args.quality}")
    log.info("=" * 60)

    conn = await asyncpg.connect(DB_DSN)
    log.info("Connected to MINDEX database")

    start = time.time()
    try:
        count = await load_observations(conn, args.days, args.taxon, args.quality)
    finally:
        await conn.close()

    elapsed = time.time() - start
    log.info("=" * 60)
    log.info(f"iNaturalist ETL complete in {elapsed:.1f}s")
    log.info(f"  Total: {count:,} observations loaded into MINDEX")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
