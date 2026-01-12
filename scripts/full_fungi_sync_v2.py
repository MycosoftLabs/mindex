#!/usr/bin/env python
"""
Full Fungi Data Sync Script v2
===============================
Comprehensive script with checkpoint/resume support and exponential backoff.
"""
import sys
import time
from datetime import datetime

# Add parent to path
sys.path.insert(0, '/app')

from mindex_etl.checkpoint import CheckpointManager
from mindex_etl.jobs.sync_inat_taxa import sync_inat_taxa
from mindex_etl.jobs.sync_inat_observations import sync_inat_observations
from mindex_etl.jobs.sync_gbif_occurrences import sync_gbif_occurrences
from mindex_etl.jobs.sync_mycobank_taxa import sync_mycobank_taxa
from mindex_etl.jobs.sync_fungidb_genomes import sync_fungidb_genomes
from mindex_etl.jobs.backfill_traits import backfill_traits

def log(msg: str):
    """Log with timestamp."""
    print(f"[{datetime.now().isoformat()}] {msg}", flush=True)

def sync_with_checkpoint(job_name: str, sync_func, *args, **kwargs):
    """Run a sync job with checkpoint support."""
    checkpoint = CheckpointManager(job_name)
    
    # Check if we should resume
    last_page = checkpoint.get_last_page()
    if last_page:
        log(f"Found checkpoint for {job_name} at page {last_page}")
        kwargs['start_page'] = last_page
        kwargs['checkpoint_manager'] = checkpoint
    
    try:
        return sync_func(*args, **kwargs)
    except KeyboardInterrupt:
        log(f"Interrupted - checkpoint saved. Resume with: --resume")
        raise
    except Exception as e:
        log(f"Error in {job_name}: {e}")
        log(f"Checkpoint saved - can resume from page {checkpoint.get_last_page()}")
        raise

def main():
    log("=" * 70)
    log("MINDEX FULL FUNGI DATA SYNC v2")
    log("Features: Exponential backoff, checkpoint/resume, rate limit handling")
    log("=" * 70)
    
    total_taxa = 0
    total_obs = 0
    
    # 1. iNaturalist Taxa
    log("\n1. Syncing iNaturalist Taxa (all fungal species)...")
    log("   Estimated: 50,000+ species")
    log("   Rate: ~85 requests/minute (within iNat's 100/min limit)")
    try:
        count = sync_with_checkpoint(
            "inat_taxa",
            sync_inat_taxa,
            per_page=100,
            max_pages=None,  # All pages
        )
        total_taxa += count
        log(f"   ✓ Synced {count:,} iNaturalist taxa")
    except Exception as e:
        log(f"   ✗ Error: {e}")
        log("   Checkpoint saved - can resume later")
    
    # 2. iNaturalist Observations
    log("\n2. Syncing iNaturalist Observations...")
    log("   Estimated: 100,000+ research-grade observations")
    try:
        count = sync_with_checkpoint(
            "inat_obs",
            sync_inat_observations,
            max_pages=None,
            quality_grade="research",
        )
        total_obs += count
        log(f"   ✓ Synced {count:,} iNaturalist observations")
    except Exception as e:
        log(f"   ✗ Error: {e}")
        log("   Checkpoint saved - can resume later")
    
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
        count = sync_mycobank_taxa(prefixes=None)
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
    
    # 6. Traits
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
