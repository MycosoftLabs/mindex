#!/usr/bin/env python3
"""
GBIF Alphabetical Sync

Downloads ALL GBIF fungal species by querying each letter prefix.
This bypasses the 10,000 offset limit in GBIF's search API.

Target: 172,327 fungal species
"""

from mindex_etl.db import db_session
from mindex_etl.taxon_canonicalizer import upsert_taxon, link_external_id
import httpx
import time
import string

FUNGI_KEY = 5  # GBIF kingdom key for Fungi


def fetch_by_prefix(client: httpx.Client, prefix: str, offset: int = 0, limit: int = 300):
    """Fetch GBIF species starting with a specific letter."""
    resp = client.get(
        "https://api.gbif.org/v1/species/search",
        params={
            "q": f"{prefix}*",
            "highertaxonKey": FUNGI_KEY,
            "rank": "SPECIES",
            "status": "ACCEPTED",
            "limit": limit,
            "offset": offset,
        },
        timeout=60.0,
        headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft)"},
    )
    resp.raise_for_status()
    return resp.json()


def sync_letter(conn, client: httpx.Client, letter: str) -> int:
    """Sync all species starting with a letter."""
    count = 0
    offset = 0
    
    while True:
        try:
            data = fetch_by_prefix(client, letter, offset)
            results = data.get("results", [])
            
            if not results:
                break
            
            for r in results:
                name = r.get("canonicalName")
                if name and name.lower().startswith(letter):
                    try:
                        tid = upsert_taxon(
                            conn,
                            canonical_name=name,
                            rank=(r.get("rank") or "species").lower(),
                            source="gbif",
                            metadata={
                                "gbif_key": r.get("key"),
                                "scientific_name": r.get("scientificName"),
                                "family": r.get("family"),
                                "genus": r.get("genus"),
                            },
                        )
                        link_external_id(
                            conn,
                            taxon_id=tid,
                            source="gbif",
                            external_id=str(r.get("key", "")),
                            metadata={},
                        )
                        count += 1
                    except Exception:
                        pass
            
            conn.commit()
            
            if data.get("endOfRecords", False) or len(results) < 300:
                break
            
            offset += 300
            
            if offset >= 9900:  # Near API limit
                break
            
            time.sleep(0.2)
            
        except Exception as e:
            print(f"  Error at offset {offset}: {e}", flush=True)
            break
    
    return count


def main():
    print("=" * 60)
    print("GBIF ALPHABETICAL FUNGI SYNC")
    print("=" * 60)
    
    total = 0
    
    with httpx.Client() as client:
        with db_session() as conn:
            # Query by each letter of the alphabet
            for letter in string.ascii_lowercase:
                print(f"Syncing '{letter.upper()}'...", end=" ", flush=True)
                count = sync_letter(conn, client, letter)
                total += count
                print(f"{count:,} taxa (total: {total:,})", flush=True)
                time.sleep(0.5)
    
    print()
    print("=" * 60)
    print(f"COMPLETE: {total:,} total taxa synced")
    print("=" * 60)


if __name__ == "__main__":
    main()
