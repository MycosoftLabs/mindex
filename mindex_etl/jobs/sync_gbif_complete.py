"""
GBIF Complete Sync

Download ALL fungal species from GBIF (150,000+)
Uses pagination with proper error handling.
"""

from __future__ import annotations

import argparse
import time
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..db import db_session
from ..taxon_canonicalizer import link_external_id, upsert_taxon


FUNGI_KEY = 5  # GBIF Fungi kingdom key


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    reraise=True,
)
def fetch_gbif_page(client: httpx.Client, offset: int, limit: int) -> dict:
    """Fetch a page of GBIF species."""
    response = client.get(
        "https://api.gbif.org/v1/species/search",
        params={
            "highertaxonKey": FUNGI_KEY,
            "rank": "SPECIES",
            "status": "ACCEPTED",
            "limit": limit,
            "offset": offset,
        },
        timeout=60.0,
        headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft)"},
    )
    response.raise_for_status()
    return response.json()


def sync_gbif_fungi(
    *,
    limit: int = 300,
    max_offset: Optional[int] = None,
    delay: float = 0.3,
) -> int:
    """
    Sync all GBIF fungal species to MINDEX.
    
    Args:
        limit: Records per page (max 300)
        max_offset: Maximum offset to fetch (None for all)
        delay: Delay between requests
    
    Returns:
        Number of records processed
    """
    print("="*60)
    print("GBIF COMPLETE FUNGI SYNC")
    print("="*60)
    
    processed = 0
    inserted = 0
    updated = 0
    errors = 0
    offset = 0
    
    with httpx.Client() as client:
        with db_session() as conn:
            while True:
                print(f"Fetching GBIF offset {offset}...", flush=True)
                
                try:
                    data = fetch_gbif_page(client, offset, limit)
                except Exception as e:
                    print(f"Error fetching offset {offset}: {e}")
                    errors += 1
                    if errors > 10:
                        print("Too many errors, stopping")
                        break
                    offset += limit
                    continue
                
                results = data.get("results", [])
                
                if not results:
                    print("No more results")
                    break
                
                for record in results:
                    try:
                        canonical_name = record.get("canonicalName")
                        if not canonical_name:
                            continue
                        
                        taxon_payload = {
                            "canonical_name": canonical_name,
                            "rank": (record.get("rank") or "species").lower(),
                            "source": "gbif",
                            "metadata": {
                                "gbif_key": record.get("key"),
                                "nub_key": record.get("nubKey"),
                                "scientific_name": record.get("scientificName"),
                                "authorship": record.get("authorship"),
                                "kingdom": record.get("kingdom"),
                                "phylum": record.get("phylum"),
                                "class": record.get("class"),
                                "order": record.get("order"),
                                "family": record.get("family"),
                                "genus": record.get("genus"),
                            },
                        }
                        
                        taxon_id = upsert_taxon(conn, **taxon_payload)
                        
                        if record.get("key"):
                            link_external_id(
                                conn,
                                taxon_id=taxon_id,
                                source="gbif",
                                external_id=str(record["key"]),
                                metadata=taxon_payload["metadata"],
                            )
                        
                        processed += 1
                        
                    except Exception as e:
                        errors += 1
                        if errors <= 5:
                            print(f"Error processing {canonical_name}: {e}")
                
                # Commit batch
                conn.commit()
                
                if processed % 3000 == 0:
                    print(f"  Processed: {processed:,}, Errors: {errors}", flush=True)
                
                if data.get("endOfRecords", False):
                    print("End of records reached")
                    break
                
                if max_offset and offset >= max_offset:
                    print(f"Reached max offset: {max_offset}")
                    break
                
                offset += limit
                time.sleep(delay)
    
    print()
    print("="*60)
    print("SYNC COMPLETE")
    print("="*60)
    print(f"Processed: {processed:,}")
    print(f"Errors: {errors}")
    
    return processed


def main():
    parser = argparse.ArgumentParser(description="Sync GBIF fungi to MINDEX")
    parser.add_argument(
        "--max-offset",
        type=int,
        default=None,
        help="Maximum offset (None for all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=300,
        help="Records per page",
    )
    
    args = parser.parse_args()
    
    total = sync_gbif_fungi(
        limit=args.limit,
        max_offset=args.max_offset,
    )
    
    print(f"\nTotal: {total:,} GBIF taxa synced")


if __name__ == "__main__":
    main()
