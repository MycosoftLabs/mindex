"""
iNaturalist Species Scraper

Authenticated scraper for iNaturalist fungal taxa.
Uses API token for higher rate limits and better access.

Target: 26,616+ fungal species
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from ..config import settings

FUNGI_TAXON_ID = 47170  # iNaturalist taxon ID for Fungi kingdom


def map_inat_taxon(record: dict) -> dict:
    """Map iNaturalist record to MINDEX taxon format."""
    return {
        "canonical_name": record.get("name"),
        "rank": record.get("rank") or "species",
        "common_name": record.get("preferred_common_name"),
        "description": record.get("wikipedia_summary"),
        "source": "inat",
        "metadata": {
            "inat_id": record.get("id"),
            "parent_id": record.get("parent_id"),
            "ancestry": record.get("ancestry"),
            "observations_count": record.get("observations_count"),
            "wikipedia_url": record.get("wikipedia_url"),
            "default_photo": record.get("default_photo"),
            "iconic_taxon_name": record.get("iconic_taxon_name"),
            "is_active": record.get("is_active"),
        },
    }


def get_auth_headers() -> dict:
    """Get authentication headers for iNaturalist API."""
    headers = {
        "User-Agent": "MINDEX-ETL/1.0 (Mycosoft Fungal Database; contact@mycosoft.org)",
        "Accept": "application/json",
    }
    
    # Add API token if available
    if settings.inat_api_token:
        headers["Authorization"] = f"Bearer {settings.inat_api_token}"
    
    return headers


def save_to_local(data: list, filename: str, subdir: str = "inat") -> str:
    """Save scraped data to local storage."""
    data_dir = Path(settings.local_data_dir) / subdir
    data_dir.mkdir(parents=True, exist_ok=True)
    
    filepath = data_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    return str(filepath)


@retry(
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=2, min=4, max=300),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
    reraise=True,
)
def _fetch_page(client: httpx.Client, page: int, per_page: int, rank: str = None) -> dict:
    """Fetch a page from iNaturalist API with exponential backoff retry."""
    params = {
        "taxon_id": FUNGI_TAXON_ID,
        "is_active": True,
        "order_by": "observations_count",
        "per_page": per_page,
        "page": page,
    }
    
    if rank:
        params["rank"] = rank
    
    response = client.get(
        f"{settings.inat_base_url}/taxa",
        params=params,
        timeout=settings.http_timeout,
        headers=get_auth_headers(),
    )
    
    # Handle rate limiting
    if response.status_code == 403:
        wait_time = 30  # Shorter wait with token
        print(f"Rate limited (403) on page {page}, waiting {wait_time}s...", flush=True)
        time.sleep(wait_time)
        response.raise_for_status()
    elif response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", 30))
        print(f"Rate limited (429) on page {page}, waiting {retry_after}s...", flush=True)
        time.sleep(retry_after)
        response.raise_for_status()
    else:
        response.raise_for_status()
        
    return response.json()


def iter_fungi_taxa(
    *,
    per_page: int = 200,  # Max allowed
    max_pages: Optional[int] = None,
    delay_seconds: float = None,
    client: Optional[httpx.Client] = None,
    save_locally: bool = True,
    rank: str = None,
) -> Generator[Dict, None, None]:
    """
    Iterate over all fungal taxa from iNaturalist.
    
    Args:
        per_page: Results per page (max 200)
        max_pages: Maximum pages to fetch (None for all)
        delay_seconds: Delay between requests (None uses config)
        client: Optional HTTP client
        save_locally: Whether to save raw data locally
        rank: Filter by rank (species, genus, family, etc.)
    
    Yields:
        Tuple of (mapped_taxon, source, external_id)
    """
    per_page = min(per_page, 200)
    delay = delay_seconds if delay_seconds is not None else settings.inat_rate_limit
    
    close_client = False
    if client is None:
        client = httpx.Client()
        close_client = True
    
    all_records = []
    
    try:
        page = 1
        total_results = None
        
        while True:
            print(f"Fetching iNaturalist page {page}...", flush=True)
            
            payload = _fetch_page(client, page, per_page, rank)
            results = payload.get("results", [])
            
            if total_results is None:
                total_results = payload.get("total_results", 0)
                print(f"Total results: {total_results}", flush=True)
            
            if not results:
                break
            
            # Save raw data locally
            if save_locally:
                all_records.extend(results)
            
            for record in results:
                mapped = map_inat_taxon(record)
                external_id = record.get("id")
                yield mapped, "inat", str(external_id)
            
            page += 1
            
            if max_pages and page > max_pages:
                print(f"Reached max pages limit ({max_pages})", flush=True)
                break
            
            # Check if we've fetched all
            if page * per_page >= total_results:
                print(f"Fetched all {total_results} results", flush=True)
                break
            
            time.sleep(delay)
        
        # Save all records locally
        if save_locally and all_records:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"inat_fungi_{rank or 'all'}_{timestamp}.json"
            filepath = save_to_local(all_records, filename)
            print(f"Saved {len(all_records)} records to {filepath}", flush=True)
            
    finally:
        if close_client:
            client.close()


def download_all_fungi_taxa(output_dir: str = None) -> str:
    """
    Download ALL fungal taxa from iNaturalist to local storage.
    
    This is a complete dump - saves everything for offline processing.
    
    Args:
        output_dir: Directory to save data (uses config default if None)
    
    Returns:
        Path to the saved JSON file
    """
    output_dir = output_dir or settings.local_data_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    all_taxa = []
    ranks = ["kingdom", "phylum", "class", "order", "family", "genus", "species", "subspecies", "variety"]
    
    print("="*60)
    print("DOWNLOADING ALL INATURALIST FUNGI DATA")
    print("="*60)
    
    with httpx.Client() as client:
        for rank in ranks:
            print(f"\nFetching rank: {rank}")
            rank_taxa = []
            
            try:
                for taxon, source, ext_id in iter_fungi_taxa(
                    client=client,
                    rank=rank,
                    save_locally=False,  # We'll save everything at the end
                    max_pages=None,  # Get all
                ):
                    rank_taxa.append({
                        "taxon": taxon,
                        "source": source,
                        "external_id": ext_id,
                    })
                
                print(f"  Found {len(rank_taxa)} {rank} taxa")
                all_taxa.extend(rank_taxa)
                
            except Exception as e:
                print(f"  Error fetching {rank}: {e}")
                continue
    
    # Save complete dump
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"inat_fungi_complete_{timestamp}.json"
    filepath = Path(output_dir) / "inat" / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({
            "downloaded_at": datetime.now().isoformat(),
            "total_taxa": len(all_taxa),
            "taxa": all_taxa,
        }, f, indent=2, ensure_ascii=False)
    
    print("="*60)
    print(f"COMPLETE: Downloaded {len(all_taxa)} total taxa")
    print(f"Saved to: {filepath}")
    print("="*60)
    
    return str(filepath)


# Observations scraper (for future use)
def iter_fungi_observations(
    *,
    taxon_id: int = FUNGI_TAXON_ID,
    per_page: int = 200,
    max_pages: Optional[int] = None,
    client: Optional[httpx.Client] = None,
) -> Generator[dict, None, None]:
    """Iterate over fungal observations from iNaturalist."""
    close_client = False
    if client is None:
        client = httpx.Client()
        close_client = True
    
    try:
        page = 1
        while True:
            response = client.get(
                f"{settings.inat_base_url}/observations",
                params={
                    "taxon_id": taxon_id,
                    "quality_grade": "research",
                    "per_page": per_page,
                    "page": page,
                },
                timeout=settings.http_timeout,
                headers=get_auth_headers(),
            )
            response.raise_for_status()
            
            payload = response.json()
            results = payload.get("results", [])
            
            if not results:
                break
            
            for obs in results:
                yield obs
            
            page += 1
            if max_pages and page > max_pages:
                break
            
            time.sleep(settings.inat_rate_limit)
            
    finally:
        if close_client:
            client.close()
