#!/usr/bin/env python3
"""Quick GBIF sync script for running in Docker container."""

from mindex_etl.db import db_session
from mindex_etl.taxon_canonicalizer import upsert_taxon, link_external_id
import httpx
import time
import sys

def main():
    print("Starting GBIF sync...", flush=True)
    processed = 0
    errors = 0
    
    with httpx.Client() as client:
        with db_session() as conn:
            for offset in range(0, 170000, 300):
                try:
                    resp = client.get(
                        "https://api.gbif.org/v1/species/search",
                        params={
                            "highertaxonKey": 5, 
                            "rank": "SPECIES", 
                            "status": "ACCEPTED", 
                            "limit": 300, 
                            "offset": offset
                        },
                        timeout=60.0
                    )
                    resp.raise_for_status()
                    results = resp.json().get("results", [])
                    
                    if not results:
                        print(f"No more results at offset {offset}", flush=True)
                        break
                    
                    for r in results:
                        name = r.get("canonicalName")
                        if name:
                            try:
                                tid = upsert_taxon(
                                    conn, 
                                    canonical_name=name, 
                                    rank="species", 
                                    source="gbif", 
                                    metadata={"gbif_key": r.get("key")}
                                )
                                link_external_id(
                                    conn, 
                                    taxon_id=tid, 
                                    source="gbif", 
                                    external_id=str(r.get("key", "")), 
                                    metadata={}
                                )
                                processed += 1
                            except Exception as e:
                                errors += 1
                    
                    conn.commit()
                    
                    if offset % 3000 == 0:
                        print(f"Offset {offset}: {processed} taxa, {errors} errors", flush=True)
                    
                    time.sleep(0.3)
                    
                except Exception as e:
                    print(f"Error at offset {offset}: {e}", flush=True)
                    errors += 1
                    if errors > 20:
                        break
                    continue
    
    print(f"Done: {processed} records, {errors} errors", flush=True)

if __name__ == "__main__":
    main()
