#!/usr/bin/env python
"""
Full Fungi Data Sync Script
===========================
Comprehensive script to pull all available fungal data from multiple sources.
This script runs ETL jobs sequentially to avoid rate limits and deadlocks.
"""
import sys
import time
from datetime import datetime

# Add parent to path
sys.path.insert(0, '/app')

from mindex_etl.jobs.sync_inat_taxa import sync_inat_taxa
from mindex_etl.jobs.sync_inat_observations import sync_inat_observations
from mindex_etl.jobs.sync_gbif_occurrences import sync_gbif_occurrences
from mindex_etl.jobs.sync_mycobank_taxa import sync_mycobank_taxa
from mindex_etl.jobs.sync_fungidb_genomes import sync_fungidb_genomes
from mindex_etl.jobs.backfill_traits import backfill_traits

def log(msg: str):
    """Log with timestamp."""
    print(f"[{datetime.now().isoformat()}] {msg}", flush=True)

def main():
    log("=" * 70)
    log("MINDEX FULL FUNGI DATA SYNC")
    log("=" * 70)
    
    total_taxa = 0
    total_obs = 0
    
    # 1. iNaturalist Taxa (largest source of fungal taxonomy)
    log("\n1. Syncing iNaturalist Taxa (all fungal species)...")
    log("   Estimated: 50,000+ species")
    log("   Rate: ~85 requests/minute (within iNat's 100/min limit)")
    try:
        count = sync_inat_taxa(per_page=100, max_pages=None)  # None = all pages
        total_taxa += count
        log(f"   ✓ Synced {count:,} iNaturalist taxa")
    except Exception as e:
        log(f"   ✗ Error: {e}")
    
    # 2. iNaturalist Observations (with locations and images)
    log("\n2. Syncing iNaturalist Observations...")
    log("   Estimated: 100,000+ research-grade observations")
    try:
        count = sync_inat_observations(max_pages=None, quality_grade="research")
        total_obs += count
        log(f"   ✓ Synced {count:,} iNaturalist observations")
    except Exception as e:
        log(f"   ✗ Error: {e}")
    
    # 3. GBIF Occurrences
    log("\n3. Syncing GBIF Occurrences...")
    log("   Estimated: 50,000+ occurrence records")
    try:
        count = sync_gbif_occurrences(max_pages=None)
        total_obs += count
        log(f"   ✓ Synced {count:,} GBIF occurrences")
    except Exception as e:
        log(f"   ✗ Error: {e}")
    
    # 4. MycoBank Taxonomy
    log("\n4. Syncing MycoBank Taxonomy...")
    log("   Estimated: 150,000+ names with synonyms")
    try:
        count = sync_mycobank_taxa(prefixes=None)  # All prefixes a-z
        log(f"   ✓ Processed {count:,} MycoBank taxa")
    except Exception as e:
        log(f"   ✗ Error: {e}")
    
    # 5. FungiDB Genomes
    log("\n5. Syncing FungiDB Genome Metadata...")
    log("   Estimated: 1,000+ genomes")
    try:
        count = sync_fungidb_genomes(max_pages=None)
        log(f"   ✓ Synced {count:,} genome records")
    except Exception as e:
        log(f"   ✗ Error: {e}")
    
    # 6. Traits (Mushroom.World + Wikipedia)
    log("\n6. Backfilling Taxon Traits...")
    log("   Enriching taxa with morphological and ecological data")
    try:
        count = backfill_traits(max_pages=None, enrich_wikipedia=True)
        log(f"   ✓ Backfilled traits for {count:,} taxa")
    except Exception as e:
        log(f"   ✗ Error: {e}")
    
    # Summary
    log("\n" + "=" * 70)
    log("SYNC COMPLETE")
    log("=" * 70)
    log(f"Total Taxa: {total_taxa:,}")
    log(f"Total Observations: {total_obs:,}")
    log("=" * 70)

if __name__ == "__main__":
    main()
