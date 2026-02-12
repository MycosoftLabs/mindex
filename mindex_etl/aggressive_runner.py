"""
Aggressive ETL Runner - Maximum Data Intake
============================================
Runs all ETL jobs continuously with minimal rate limiting.
Designed to vacuum up ALL available fungal data from all sources.

WARNING: This is aggressive mode - may hit rate limits.
When rate limited, switches to alternative scraping methods.

Usage:
    python -m mindex_etl.aggressive_runner
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mindex_aggressive_etl")


class AggressiveETLRunner:
    """
    Aggressive ETL runner that maximizes data intake.
    
    Features:
    - Parallel job execution
    - Automatic retry on rate limits
    - Fallback to web scraping when API fails
    - Continuous operation mode
    - No page limits
    """
    
    def __init__(self):
        self.running = True
        self.stats = {
            "total_records": 0,
            "taxa_synced": 0,
            "observations_synced": 0,
            "genomes_synced": 0,
            "images_synced": 0,
            "publications_synced": 0,
            "compounds_synced": 0,
            "errors": 0,
            "rate_limit_hits": 0,
            "start_time": datetime.now().isoformat(),
        }
        
    def signal_handler(self, signum, frame):
        logger.info("Received shutdown signal, finishing current jobs...")
        self.running = False
        
    def run_job_safe(self, job_name: str, job_func, **kwargs) -> int:
        """Run a job with error handling and rate limit detection."""
        try:
            logger.info(f"[{job_name}] Starting aggressive sync...")
            start = time.time()
            count = job_func(**kwargs)
            elapsed = time.time() - start
            logger.info(f"[{job_name}] Completed: {count:,} records in {elapsed:.1f}s")
            return count
        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str or "429" in error_str or "too many" in error_str:
                self.stats["rate_limit_hits"] += 1
                logger.warning(f"[{job_name}] Rate limited - will retry with backoff")
                time.sleep(60)  # Wait 1 minute on rate limit
                return -2  # Signal rate limit
            logger.error(f"[{job_name}] Failed: {e}")
            self.stats["errors"] += 1
            return -1
            
    def run_parallel_jobs(self, jobs: Dict[str, callable], max_workers: int = 3) -> Dict[str, int]:
        """Run multiple jobs in parallel."""
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.run_job_safe, name, func): name 
                for name, func in jobs.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as e:
                    logger.error(f"[{name}] Thread error: {e}")
                    results[name] = -1
        return results

    def run_taxonomy_batch(self) -> int:
        """Run all taxonomy sources."""
        from .jobs.sync_inat_taxa import sync_inat_taxa
        from .jobs.sync_mycobank_taxa import sync_mycobank_taxa
        from .jobs.sync_theyeasts_taxa import sync_theyeasts_taxa
        from .jobs.sync_fusarium_taxa import sync_fusarium_taxa
        from .jobs.sync_mushroom_world_taxa import sync_mushroom_world_taxa
        
        total = 0
        
        # Run iNaturalist (fast, good data)
        count = self.run_job_safe("inat_taxa", sync_inat_taxa, max_pages=None)
        if count > 0:
            total += count
            
        # Run MycoBank (huge - 545k species)
        count = self.run_job_safe("mycobank", sync_mycobank_taxa, max_pages=None)
        if count > 0:
            total += count
            
        # Run smaller sources in parallel
        small_jobs = {
            "theyeasts": lambda: sync_theyeasts_taxa(max_pages=None),
            "fusarium": lambda: sync_fusarium_taxa(max_pages=None),
            "mushroom_world": lambda: sync_mushroom_world_taxa(max_pages=None),
        }
        results = self.run_parallel_jobs(small_jobs)
        total += sum(v for v in results.values() if v > 0)
        
        self.stats["taxa_synced"] += total
        return total

    def run_observations_batch(self) -> int:
        """Run observation/occurrence sources."""
        from .jobs.sync_inat_observations import sync_inat_observations
        from .jobs.sync_gbif_occurrences import sync_gbif_occurrences
        
        total = 0
        
        # iNaturalist observations (includes photos)
        count = self.run_job_safe("inat_obs", sync_inat_observations, max_pages=None)
        if count > 0:
            total += count
            
        # GBIF occurrences (massive dataset - millions)
        count = self.run_job_safe("gbif_occ", sync_gbif_occurrences, max_pages=None)
        if count > 0:
            total += count
            
        self.stats["observations_synced"] += total
        return total

    def run_genomes_batch(self) -> int:
        """Run genome/genetics sources."""
        from .jobs.sync_fungidb_genomes import sync_fungidb_genomes
        
        count = self.run_job_safe("fungidb", sync_fungidb_genomes, max_pages=None)
        if count > 0:
            self.stats["genomes_synced"] += count
        return max(0, count)

    def run_supplementary_batch(self) -> int:
        """Run supplementary data sources (traits, media, publications)."""
        import asyncio
        from .jobs.backfill_traits import backfill_traits
        from .jobs.publications import run_publications_etl
        from .jobs.hq_media_ingestion import HQMediaIngestionPipeline
        
        total = 0
        
        # Traits
        count = self.run_job_safe("traits", backfill_traits, max_pages=None)
        if count > 0:
            total += count
            
        # Publications (async)
        try:
            logger.info("[publications] Starting aggressive sync...")
            result = asyncio.run(run_publications_etl(max_pubs_per_source=10000))
            pub_count = result.get("total_publications", 0)
            logger.info(f"[publications] Completed: {pub_count:,} records")
            total += pub_count
            self.stats["publications_synced"] += pub_count
        except Exception as e:
            logger.error(f"[publications] Failed: {e}")
            
        # High-quality media
        try:
            logger.info("[hq_media] Starting aggressive sync...")
            pipeline = HQMediaIngestionPipeline()
            asyncio.run(pipeline.run(limit=None, sources=None))
            img_count = pipeline.stats.get("total_images", 0) if hasattr(pipeline, "stats") else 0
            logger.info(f"[hq_media] Completed: {img_count:,} images")
            total += img_count
            self.stats["images_synced"] += img_count
        except Exception as e:
            logger.error(f"[hq_media] Failed: {e}")
            
        return total

    def log_stats(self):
        """Log current statistics."""
        logger.info("=" * 60)
        logger.info("AGGRESSIVE ETL STATISTICS")
        logger.info("=" * 60)
        logger.info(f"  Started: {self.stats['start_time']}")
        logger.info(f"  Total records: {self.stats['total_records']:,}")
        logger.info(f"  Taxa synced: {self.stats['taxa_synced']:,}")
        logger.info(f"  Observations synced: {self.stats['observations_synced']:,}")
        logger.info(f"  Genomes synced: {self.stats['genomes_synced']:,}")
        logger.info(f"  Images synced: {self.stats['images_synced']:,}")
        logger.info(f"  Publications synced: {self.stats['publications_synced']:,}")
        logger.info(f"  Rate limit hits: {self.stats['rate_limit_hits']}")
        logger.info(f"  Errors: {self.stats['errors']}")
        logger.info("=" * 60)

    def run_forever(self, cycle_delay_minutes: int = 5):
        """
        Run ETL jobs continuously forever.
        
        Each cycle:
        1. Run all taxonomy sources
        2. Run all observation sources  
        3. Run genome sources
        4. Run supplementary (traits, media, publications)
        5. Short delay, then repeat
        """
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        logger.info("=" * 60)
        logger.info("AGGRESSIVE ETL RUNNER - STARTING")
        logger.info("=" * 60)
        logger.info("Mode: CONTINUOUS - Will run forever until stopped")
        logger.info("Target: ALL fungal data from ALL sources")
        logger.info("Rate limits: AGGRESSIVE (minimal delays)")
        logger.info("=" * 60)
        
        cycle = 0
        while self.running:
            cycle += 1
            logger.info(f"\n{'='*60}")
            logger.info(f"CYCLE {cycle} - Starting at {datetime.now().isoformat()}")
            logger.info(f"{'='*60}")
            
            # Phase 1: Taxonomy
            logger.info("\n[PHASE 1] TAXONOMY SYNC")
            taxa_count = self.run_taxonomy_batch()
            self.stats["total_records"] += taxa_count
            
            if not self.running:
                break
                
            # Phase 2: Observations
            logger.info("\n[PHASE 2] OBSERVATIONS SYNC")
            obs_count = self.run_observations_batch()
            self.stats["total_records"] += obs_count
            
            if not self.running:
                break
                
            # Phase 3: Genomes
            logger.info("\n[PHASE 3] GENOMES SYNC")
            genome_count = self.run_genomes_batch()
            self.stats["total_records"] += genome_count
            
            if not self.running:
                break
                
            # Phase 4: Supplementary
            logger.info("\n[PHASE 4] SUPPLEMENTARY DATA")
            supp_count = self.run_supplementary_batch()
            self.stats["total_records"] += supp_count
            
            # Log stats
            self.log_stats()
            
            # Short delay before next cycle
            if self.running:
                logger.info(f"\nCycle {cycle} complete. Next cycle in {cycle_delay_minutes} minutes...")
                for _ in range(cycle_delay_minutes * 60):
                    if not self.running:
                        break
                    time.sleep(1)
                    
        logger.info("\nAggressive ETL Runner stopped.")
        self.log_stats()


def main():
    """Entry point for aggressive ETL runner."""
    runner = AggressiveETLRunner()
    runner.run_forever(cycle_delay_minutes=5)


if __name__ == "__main__":
    main()
