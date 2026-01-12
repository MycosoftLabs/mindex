"""
iNaturalist Observations Sync
=============================
Sync fungal observations with locations, images, and metadata from iNaturalist.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import Dict, Generator, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from ..checkpoint import CheckpointManager
from ..config import settings
from ..db import db_session
from ..taxon_canonicalizer import upsert_taxon

FUNGI_TAXON_ID = 47170  # iNaturalist Fungi taxon ID


@retry(
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=2, min=4, max=300),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
    reraise=True,
)
def _fetch_observations(
    client: httpx.Client,
    page: int,
    per_page: int,
    quality_grade: str = "research",
    updated_since: Optional[str] = None,
) -> dict:
    """Fetch observations from iNaturalist API with exponential backoff retry."""
    import time
    
    params = {
        "taxon_id": FUNGI_TAXON_ID,
        "quality_grade": quality_grade,
        "per_page": per_page,
        "page": page,
        "order_by": "observed_on",
        "order": "desc",
        "photos": "true",
        "geo": "true",
    }
    if updated_since:
        params["updated_since"] = updated_since

    try:
        response = client.get(
            f"{settings.inat_base_url}/observations",
            params=params,
            timeout=settings.http_timeout,
            headers={"User-Agent": "mindex-etl/0.1"},
        )
        
        # Handle rate limiting specifically
        if response.status_code == 403:
            wait_time = 60
            print(f"Rate limited (403) on page {page}, waiting {wait_time}s...", flush=True)
            time.sleep(wait_time)
            response.raise_for_status()
        elif response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            print(f"Rate limited (429) on page {page}, waiting {retry_after}s...", flush=True)
            time.sleep(retry_after)
            response.raise_for_status()
        else:
            response.raise_for_status()
            
        return response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 429):
            raise
        raise


def iter_observations(
    *,
    per_page: int = 100,
    max_pages: Optional[int] = None,
    quality_grade: str = "research",
    updated_since: Optional[str] = None,
    delay_seconds: float = 0.7,  # Increased to respect rate limits
) -> Generator[Dict, None, None]:
    """Iterate through iNaturalist fungal observations."""
    with httpx.Client() as client:
        page = 1
        while True:
            payload = _fetch_observations(
                client, page, per_page, quality_grade, updated_since
            )
            results = payload.get("results", [])
            if not results:
                break

            for obs in results:
                yield _map_observation(obs)

            page += 1
            if max_pages and page > max_pages:
                break
            time.sleep(delay_seconds)


def _map_observation(obs: dict) -> dict:
    """Map iNaturalist observation to MINDEX format."""
    # Extract location
    lat = obs.get("geojson", {}).get("coordinates", [None, None])[1]
    lng = obs.get("geojson", {}).get("coordinates", [None, None])[0]

    # Extract photos
    photos = []
    for photo in obs.get("photos", []):
        photos.append({
            "url": photo.get("url", "").replace("square", "large"),
            "attribution": photo.get("attribution"),
            "license_code": photo.get("license_code"),
        })

    # Extract taxon info
    taxon = obs.get("taxon") or {}

    return {
        "source": "inat",
        "source_id": str(obs.get("id")),
        "observed_at": obs.get("observed_on") or obs.get("created_at"),
        "observer": obs.get("user", {}).get("login"),
        "lat": lat,
        "lng": lng,
        "accuracy_m": obs.get("positional_accuracy"),
        "taxon_name": taxon.get("name"),
        "taxon_rank": taxon.get("rank", "species"),
        "taxon_common_name": taxon.get("preferred_common_name"),
        "taxon_inat_id": taxon.get("id"),
        "photos": photos,
        "notes": obs.get("description"),
        "quality_grade": obs.get("quality_grade"),
        "metadata": {
            "inat_id": obs.get("id"),
            "uri": obs.get("uri"),
            "place_guess": obs.get("place_guess"),
            "identifications_count": obs.get("identifications_count"),
            "comments_count": obs.get("comments_count"),
            "faves_count": obs.get("faves_count"),
        },
    }


def sync_inat_observations(
    *,
    max_pages: Optional[int] = None,
    quality_grade: str = "research",
    start_page: int = 1,
    checkpoint_manager: Optional[CheckpointManager] = None,
) -> int:
    """Sync iNaturalist observations into MINDEX database with checkpoint support."""
    inserted = 0
    checkpoint_interval = 10  # Save checkpoint every 10 pages
    page = start_page

    with db_session() as conn:
        for obs in iter_observations(max_pages=max_pages, quality_grade=quality_grade):
            taxon_name = obs.get("taxon_name")
            if not taxon_name:
                continue

            # Upsert taxon
            taxon_id = upsert_taxon(
                conn,
                canonical_name=taxon_name,
                rank=obs.get("taxon_rank", "species"),
                common_name=obs.get("taxon_common_name"),
                source="inat",
            )

            # Insert observation
            with conn.cursor() as cur:
                # Create point geometry if coordinates available
                location_sql = "NULL"
                location_params = []
                if obs.get("lat") and obs.get("lng"):
                    location_sql = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography"
                    location_params = [obs["lng"], obs["lat"]]

                # Check if observation already exists
                cur.execute(
                    "SELECT 1 FROM obs.observation WHERE source = %s AND source_id = %s",
                    (obs["source"], obs["source_id"]),
                )
                exists = cur.fetchone()

                if exists:
                    # Update existing
                    update_sql = f"""
                        UPDATE obs.observation SET
                            taxon_id = %s,
                            observer = %s,
                            location = {location_sql},
                            accuracy_m = %s,
                            media = %s::jsonb,
                            notes = %s,
                            metadata = %s::jsonb
                        WHERE source = %s AND source_id = %s
                    """
                    cur.execute(
                        update_sql,
                        (
                            taxon_id,
                            obs.get("observer"),
                            *location_params,
                            obs.get("accuracy_m"),
                            json.dumps(obs.get("photos", [])),
                            obs.get("notes"),
                            json.dumps(obs.get("metadata", {})),
                            obs["source"],
                            obs["source_id"],
                        ),
                    )
                else:
                    # Insert new
                    insert_sql = f"""
                        INSERT INTO obs.observation (
                            taxon_id, source, source_id, observer, observed_at,
                            location, accuracy_m, media, notes, metadata
                        )
                        VALUES (
                            %s, %s, %s, %s, %s::timestamptz,
                            {location_sql}, %s, %s::jsonb, %s, %s::jsonb
                        )
                    """
                    cur.execute(
                        insert_sql,
                        (
                            taxon_id,
                            obs["source"],
                            obs["source_id"],
                            obs.get("observer"),
                            obs.get("observed_at"),
                            *location_params,
                            obs.get("accuracy_m"),
                            json.dumps(obs.get("photos", [])),
                            obs.get("notes"),
                            json.dumps(obs.get("metadata", {})),
                    ),
                )

            inserted += 1
            
            # Save checkpoint periodically
            if checkpoint_manager and inserted % (100 * checkpoint_interval) == 0:
                checkpoint_manager.save(page, records_processed=inserted)
                print(f"Checkpoint saved: page {page}, {inserted} observations", flush=True)
            
            # Track current page (approximate)
            if inserted % 100 == 0:
                page += 1
    
    # Final checkpoint
    if checkpoint_manager:
        checkpoint_manager.save(page, records_processed=inserted, completed=True)
    
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync iNaturalist fungal observations")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--quality-grade", default="research", choices=["research", "needs_id", "casual"])
    args = parser.parse_args()

    total = sync_inat_observations(max_pages=args.max_pages, quality_grade=args.quality_grade)
    print(f"Synced {total} iNaturalist observations")


if __name__ == "__main__":
    main()
