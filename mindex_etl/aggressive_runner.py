"""
Aggressive ETL Runner - MAXIMUM Data Intake
============================================
Runs ALL ETL jobs continuously with minimal rate limiting.
Designed to vacuum up ALL available fungal data from ALL sources.

Sources:
- iNaturalist (taxa + observations)
- GBIF (species + occurrences) 
- MycoBank (545k+ species)
- FungiDB (genomes + annotations)
- GenBank/NCBI (sequences + genomes)
- PubChem (compounds + mycotoxins)
- ChemSpider (chemical structures)
- TheYeasts.org (yeast species)
- Fusarium.org (Fusarium species)
- Mushroom.World (mushroom database)
- Index Fungorum (nomenclature)
- Wikipedia (species descriptions)
- PubMed (publications)

WARNING: This is AGGRESSIVE mode - may hit rate limits.
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
from typing import Dict, List, Optional, Callable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mindex_aggressive_etl")


class ServiceDowntimeError(Exception):
    """Raised when a service is down (503)."""
    pass


class AggressiveETLRunner:
    """
    Aggressive ETL runner that maximizes data intake from ALL fungal data sources.
    
    Features:
    - Parallel job execution
    - Automatic retry on rate limits
    - Fallback to web scraping when API fails
    - Continuous operation mode
    - No page limits
    - All fungal databases covered
    """
    
    def __init__(self):
        self.running = True
        # Per-source cooldowns (epoch seconds). Used to avoid hammering services during outages.
        self._cooldowns: Dict[str, float] = {}
        self.stats = {
            "total_records": 0,
            "taxa_synced": 0,
            "observations_synced": 0,
            "genomes_synced": 0,
            "sequences_synced": 0,
            "images_synced": 0,
            "publications_synced": 0,
            "compounds_synced": 0,
            "errors": 0,
            "rate_limit_hits": 0,
            "sources_attempted": [],
            "sources_succeeded": [],
            "sources_failed": [],
            "start_time": datetime.now().isoformat(),
        }

    def _reset_cycle_source_lists(self) -> None:
        """
        Keep numeric counters cumulative, but reset per-cycle source lists so:
        - We don't permanently mark sources as failed after a transient blip
        - We don't trigger the fallback scraper every cycle forever
        """
        self.stats["sources_attempted"] = []
        self.stats["sources_succeeded"] = []
        self.stats["sources_failed"] = []
        
    def signal_handler(self, signum, frame):
        logger.info("Received shutdown signal, finishing current jobs...")
        self.running = False
        
    def run_job_safe(self, job_name: str, job_func: Callable, **kwargs) -> int:
        """Run a job with error handling and rate limit detection."""
        try:
            cooldown_until = self._cooldowns.get(job_name)
            if cooldown_until and time.time() < cooldown_until:
                remaining = int(cooldown_until - time.time())
                logger.info(f"[{job_name}] Cooldown active, skipping for {remaining}s")
                return -3

            logger.info(f"[{job_name}] Starting aggressive sync...")
            self.stats["sources_attempted"].append(job_name)
            start = time.time()
            raw = job_func(**kwargs)
            # Some legacy jobs return a stats dict instead of an int.
            # Normalize so the orchestrator can log + make decisions.
            count = raw
            if isinstance(raw, dict):
                for key in ("total_processed", "total_records", "total", "inserted", "synced", "count"):
                    if key in raw and isinstance(raw.get(key), (int, float)):
                        count = int(raw.get(key) or 0)
                        break
                else:
                    # If dict contains no obvious counter, treat as 0 records.
                    count = 0
            elif not isinstance(raw, (int, float)):
                # Anything else: treat as 0 so we don't crash formatting.
                count = 0
            elapsed = time.time() - start
            logger.info(f"[{job_name}] Completed: {count:,} records in {elapsed:.1f}s")
            if job_name not in self.stats["sources_succeeded"]:
                self.stats["sources_succeeded"].append(job_name)
            return count
        except ServiceDowntimeError as e:
            logger.warning(f"[{job_name}] Service down (503) - skipping")
            # Back off hard on downtime to prevent error spam and wasted cycles.
            self._cooldowns[job_name] = time.time() + (6 * 60 * 60)  # 6 hours
            if job_name not in self.stats["sources_failed"]:
                self.stats["sources_failed"].append(job_name)
            return -3  # Service down
        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str or "429" in error_str or "too many" in error_str:
                self.stats["rate_limit_hits"] += 1
                logger.warning(f"[{job_name}] Rate limited - will retry with backoff")
                time.sleep(60)  # Wait 1 minute on rate limit
                return -2  # Signal rate limit
            elif "503" in error_str or "downtime" in error_str:
                logger.warning(f"[{job_name}] Service down - skipping")
                self._cooldowns[job_name] = time.time() + (6 * 60 * 60)  # 6 hours
                if job_name not in self.stats["sources_failed"]:
                    self.stats["sources_failed"].append(job_name)
                return -3  # Service down
            logger.error(f"[{job_name}] Failed: {e}")
            self.stats["errors"] += 1
            if job_name not in self.stats["sources_failed"]:
                self.stats["sources_failed"].append(job_name)
            return -1
            
    def run_parallel_jobs(self, jobs: Dict[str, Callable], max_workers: int = 3) -> Dict[str, int]:
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

    # =========================================================================
    # PHASE 1: TAXONOMY - All taxonomic databases
    # =========================================================================
    
    def run_taxonomy_batch(self) -> int:
        """Run ALL taxonomy sources - GBIF, MycoBank, iNaturalist, etc."""
        total = 0
        
        # Prioritize by reliability: GBIF > MycoBank > TheYeasts > Fusarium > iNat
        taxonomy_sources = [
            # GBIF - Very reliable, massive dataset
            ("gbif_species", ".jobs.sync_gbif_occurrences", "sync_gbif_occurrences", {"max_pages": None, "sync_species": True, "sync_occurrences": False}),
            
            # MycoBank - 545,000+ fungal species
            ("mycobank", ".jobs.sync_mycobank_taxa", "sync_mycobank_taxa_compat", {"max_pages": None}),
            
            # TheYeasts.org - Comprehensive yeast database  
            ("theyeasts", ".jobs.sync_theyeasts_taxa", "sync_theyeasts_taxa", {"max_pages": None}),
            
            # Fusarium.org - Fusarium species database
            ("fusarium", ".jobs.sync_fusarium_taxa", "sync_fusarium_taxa", {"max_pages": None}),
            
            # Mushroom.World - Mushroom species
            ("mushroom_world", ".jobs.sync_mushroom_world_taxa", "sync_mushroom_world_taxa", {"max_pages": None}),
            
            # iNaturalist - Often down, try last
            ("inat_taxa", ".jobs.sync_inat_taxa", "sync_inat_taxa", {"max_pages": None}),
        ]
        
        for name, module_path, func_name, kwargs in taxonomy_sources:
            if not self.running:
                break
            try:
                import importlib
                module = importlib.import_module(module_path, package="mindex_etl")
                sync_func = getattr(module, func_name)
                
                # Quick test - try with small batch first
                logger.info(f"[{name}] Testing connection with small batch...")
                test_count = self.run_job_safe(f"{name}_test", sync_func, max_pages=3, **{k: v for k, v in kwargs.items() if k in {"sync_species", "sync_occurrences"}})
                
                if test_count == -3:  # Service down
                    logger.warning(f"[{name}] Service down, skipping")
                    continue
                elif test_count == -2:  # Rate limited
                    logger.warning(f"[{name}] Rate limited, skipping")
                    continue
                elif test_count < 0:  # Other error
                    logger.warning(f"[{name}] Failed, skipping")
                    continue
                elif test_count >= 0:
                    total += test_count
                    # Source works - do FULL sync with no limits
                    logger.info(f"[{name}] Test OK ({test_count}), starting FULL sync...")
                    full_count = self.run_job_safe(f"{name}_full", sync_func, **kwargs)
                    if full_count > 0:
                        total += full_count
                        
            except Exception as e:
                logger.error(f"[{name}] Import/run error: {e}")
                continue
        
        self.stats["taxa_synced"] += total
        return total

    # =========================================================================
    # PHASE 2: OBSERVATIONS - Geographic occurrence data
    # =========================================================================
    
    def run_observations_batch(self) -> int:
        """Run ALL observation/occurrence sources."""
        total = 0
        
        # GBIF first (reliable), then iNat
        observation_sources = [
            ("gbif_occ", ".jobs.sync_gbif_occurrences", "sync_gbif_occurrences", {"max_pages": None, "sync_species": False, "sync_occurrences": True}),
            ("inat_obs", ".jobs.sync_inat_observations", "sync_inat_observations", {"max_pages": 100}),
        ]
        
        for name, module_path, func_name, kwargs in observation_sources:
            if not self.running:
                break
            try:
                import importlib
                module = importlib.import_module(module_path, package="mindex_etl")
                sync_func = getattr(module, func_name)
                
                logger.info(f"[{name}] Starting observation sync...")
                count = self.run_job_safe(name, sync_func, **kwargs)
                
                if count > 0:
                    total += count
                    # Continue with more batches for large datasets
                    for batch_num in range(1, 20):  # Up to 20 additional batches
                        if not self.running:
                            break
                        batch_count = self.run_job_safe(f"{name}_batch{batch_num}", sync_func, max_pages=100, **{k: v for k, v in kwargs.items() if k in {"sync_species", "sync_occurrences"}})
                        if batch_count > 0:
                            total += batch_count
                        elif batch_count < 0:
                            break  # Stop on error
                            
            except Exception as e:
                logger.error(f"[{name}] Error: {e}")
                continue
                
        self.stats["observations_synced"] += total
        return total

    # =========================================================================
    # PHASE 3: GENOMES & SEQUENCES - Genetic data
    # =========================================================================
    
    def run_genomes_batch(self) -> int:
        """Run FungiDB genomes and GenBank sequences."""
        total = 0
        
        # FungiDB genomes
        try:
            from .jobs.sync_fungidb_genomes import sync_fungidb_genomes
            count = self.run_job_safe("fungidb_genomes", sync_fungidb_genomes, max_pages=None)
            if count > 0:
                total += count
                self.stats["genomes_synced"] += count
        except Exception as e:
            logger.error(f"[fungidb_genomes] Failed to import: {e}")
            
        # GenBank sequences
        try:
            from .jobs.sync_genbank_genomes import sync_genbank_genomes, sync_genbank_its_sequences
            
            # Full genome records
            # Keep per-cycle runtime bounded so we reach PubChem/publications quickly.
            count = self.run_job_safe("genbank_genomes", sync_genbank_genomes, max_pages=5)
            if count > 0:
                total += count
                self.stats["genomes_synced"] += count
                
            # ITS barcode sequences (most important for fungi identification)
            count = self.run_job_safe("genbank_its", sync_genbank_its_sequences, max_pages=5)
            if count > 0:
                total += count
                self.stats["sequences_synced"] += count
                
        except Exception as e:
            logger.error(f"[genbank] Failed to import: {e}")
            
        return total

    # =========================================================================
    # PHASE 4: CHEMISTRY - Compounds, mycotoxins
    # =========================================================================
    
    def run_chemistry_batch(self) -> int:
        """Run PubChem and ChemSpider compound syncs."""
        total = 0
        
        # PubChem - fungal compounds and mycotoxins
        try:
            from .jobs.sync_pubchem_compounds import sync_pubchem_compounds, sync_mycotoxins
            
            # All fungal compounds
            count = self.run_job_safe("pubchem_fungal", sync_pubchem_compounds, max_results=5000)
            if count > 0:
                total += count
                self.stats["compounds_synced"] += count
                
            # Specific mycotoxins
            count = self.run_job_safe("pubchem_mycotoxins", sync_mycotoxins, max_results=500)
            if count > 0:
                total += count
                self.stats["compounds_synced"] += count
                
        except Exception as e:
            logger.error(f"[pubchem] Failed to import: {e}")
            
        # ChemSpider
        try:
            from .jobs.sync_chemspider_compounds import sync_chemspider_compounds
            count = self.run_job_safe("chemspider", sync_chemspider_compounds, max_results=1000)
            if count > 0:
                total += count
                self.stats["compounds_synced"] += count
        except Exception as e:
            logger.error(f"[chemspider] Failed to import: {e}")
            
        return total

    # =========================================================================
    # PHASE 5: PUBLICATIONS - Research papers
    # =========================================================================
    
    def run_publications_batch(self) -> int:
        """Run publication syncs from PubMed and other sources."""
        total = 0
        
        try:
            from .jobs.publications import run_publications_etl
            
            logger.info("[publications] Starting aggressive sync...")
            # `run_publications_etl` expects `max_per_term`.
            result = asyncio.run(run_publications_etl(max_per_term=250))
            pub_count = int(result.get("inserted", 0) or 0)
            logger.info(f"[publications] Completed: {pub_count:,} records")
            total += pub_count
            self.stats["publications_synced"] += pub_count
        except Exception as e:
            logger.error(f"[publications] Failed: {e}")
            
        return total

    # =========================================================================
    # PHASE 6: MEDIA - Images and multimedia
    # =========================================================================
    
    def run_media_batch(self) -> int:
        """Run high-quality media ingestion."""
        total = 0
        
        try:
            from .jobs.hq_media_ingestion import HQMediaIngestionPipeline
            
            logger.info("[hq_media] Starting aggressive sync...")
            pipeline = HQMediaIngestionPipeline()
            asyncio.run(pipeline.run(limit=None, sources=None))
            img_count = pipeline.stats.get("total_images", 0) if hasattr(pipeline, "stats") else 0
            logger.info(f"[hq_media] Completed: {img_count:,} images")
            total += img_count
            self.stats["images_synced"] += img_count
        except Exception as e:
            logger.error(f"[hq_media] Failed: {e}")
            
        # iNaturalist photos
        try:
            from .jobs.backfill_inat_taxon_photos import backfill_inat_taxon_photos
            count = self.run_job_safe("inat_photos", backfill_inat_taxon_photos, max_taxa=1000)
            if count > 0:
                total += count
                self.stats["images_synced"] += count
        except Exception as e:
            logger.error(f"[inat_photos] Failed: {e}")
            
        return total

    # =========================================================================
    # PHASE 7: TRAITS - Functional traits data
    # =========================================================================
    
    def run_traits_batch(self) -> int:
        """Run trait backfill."""
        total = 0
        
        try:
            from .jobs.backfill_traits import backfill_traits
            count = self.run_job_safe("traits", backfill_traits, max_pages=None)
            if count > 0:
                total += count
        except Exception as e:
            logger.error(f"[traits] Failed: {e}")
            
        return total

    # =========================================================================
    # PHASE 8: WEB SCRAPING FALLBACK - When APIs fail
    # =========================================================================
    
    def run_web_scraping_fallback(self) -> int:
        """Fallback to aggressive web scraping when APIs fail."""
        # Disabled for now:
        # - It currently does not persist results to MINDEX (TODOs)
        # - It can generate huge volumes of requests with zero stored output
        logger.info("[web_scrape] Fallback scrapers disabled (no DB persistence yet).")
        return 0
    
    def log_stats(self):
        """Log current statistics."""
        logger.info("=" * 70)
        logger.info("AGGRESSIVE ETL STATISTICS - ALL FUNGAL DATA")
        logger.info("=" * 70)
        logger.info(f"  Started: {self.stats['start_time']}")
        logger.info(f"  Total records: {self.stats['total_records']:,}")
        logger.info(f"  Taxa synced: {self.stats['taxa_synced']:,}")
        logger.info(f"  Observations synced: {self.stats['observations_synced']:,}")
        logger.info(f"  Genomes synced: {self.stats['genomes_synced']:,}")
        logger.info(f"  Sequences synced: {self.stats['sequences_synced']:,}")
        logger.info(f"  Compounds synced: {self.stats['compounds_synced']:,}")
        logger.info(f"  Images synced: {self.stats['images_synced']:,}")
        logger.info(f"  Publications synced: {self.stats['publications_synced']:,}")
        logger.info(f"  Rate limit hits: {self.stats['rate_limit_hits']}")
        logger.info(f"  Errors: {self.stats['errors']}")
        logger.info(f"  Sources attempted: {len(self.stats['sources_attempted'])}")
        logger.info(f"  Sources succeeded: {len(self.stats['sources_succeeded'])}")
        logger.info(f"  Sources failed: {self.stats['sources_failed']}")
        logger.info("=" * 70)

    def run_forever(self, cycle_delay_minutes: int = 2):
        """
        Run ALL ETL jobs continuously forever.
        
        Each cycle:
        1. Taxonomy - GBIF, MycoBank, iNaturalist, TheYeasts, Fusarium, Mushroom.World
        2. Observations - GBIF occurrences, iNaturalist observations
        3. Genomes/Sequences - FungiDB, GenBank, NCBI
        4. Chemistry - PubChem, ChemSpider
        5. Publications - PubMed
        6. Media - Images from all sources
        7. Traits - Functional trait data
        8. Web Scraping Fallback (when APIs fail)
        
        Repeat forever with minimal delay.
        """
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        logger.info("=" * 70)
        logger.info("AGGRESSIVE ETL RUNNER - MAXIMUM FUNGAL DATA INTAKE")
        logger.info("=" * 70)
        logger.info("Mode: CONTINUOUS - Will run forever until stopped")
        logger.info("Target: ALL fungal data from ALL sources:")
        logger.info("  - iNaturalist (taxa + observations)")
        logger.info("  - GBIF (species + occurrences)")
        logger.info("  - MycoBank (545k+ species)")
        logger.info("  - FungiDB (genomes)")
        logger.info("  - GenBank/NCBI (sequences + genomes)")
        logger.info("  - PubChem (compounds + mycotoxins)")
        logger.info("  - ChemSpider (chemical structures)")
        logger.info("  - TheYeasts.org (yeast species)")
        logger.info("  - Fusarium.org (Fusarium species)")
        logger.info("  - Mushroom.World (mushroom database)")
        logger.info("  - Index Fungorum (nomenclature)")
        logger.info("  - Wikipedia (descriptions)")
        logger.info("  - PubMed (publications)")
        logger.info("Rate limits: AGGRESSIVE (minimal delays)")
        logger.info("=" * 70)
        
        cycle = 0
        while self.running:
            cycle += 1
            self._reset_cycle_source_lists()
            logger.info(f"\n{'='*70}")
            logger.info(f"CYCLE {cycle} - Starting at {datetime.now().isoformat()}")
            logger.info(f"{'='*70}")
            
            # Phase 3: Genetic data
            logger.info("\n[PHASE 3] GENOMES & SEQUENCES - FungiDB + GenBank")
            genome_count = self.run_genomes_batch()
            self.stats["total_records"] += genome_count
            
            if not self.running:
                break
            
            # Phase 4: Chemistry
            logger.info("\n[PHASE 4] CHEMISTRY - PubChem + ChemSpider")
            chem_count = self.run_chemistry_batch()
            self.stats["total_records"] += chem_count
            
            if not self.running:
                break
            
            # Phase 5: Publications
            logger.info("\n[PHASE 5] PUBLICATIONS - PubMed")
            pub_count = self.run_publications_batch()
            self.stats["total_records"] += pub_count
            
            if not self.running:
                break

            # Phase 1: Taxonomy from all sources
            # Run AFTER chemistry/genetics/publications so those tables start filling immediately,
            # even if taxonomy takes a long time per cycle.
            logger.info("\n[PHASE 1] TAXONOMY - ALL SOURCES (GBIF, MycoBank, iNat, etc)")
            taxa_count = self.run_taxonomy_batch()
            self.stats["total_records"] += taxa_count
            
            if not self.running:
                break
                
            # Phase 2: Geographic observations
            logger.info("\n[PHASE 2] OBSERVATIONS - GBIF + iNaturalist")
            obs_count = self.run_observations_batch()
            self.stats["total_records"] += obs_count
            
            if not self.running:
                break
            
            # Phase 6: Media
            logger.info("\n[PHASE 6] MEDIA - Images from all sources")
            media_count = self.run_media_batch()
            self.stats["total_records"] += media_count
            
            if not self.running:
                break
            
            # Phase 7: Traits
            logger.info("\n[PHASE 7] TRAITS - Functional trait data")
            trait_count = self.run_traits_batch()
            self.stats["total_records"] += trait_count
            
            if not self.running:
                break
            
            # Phase 8 disabled (see `run_web_scraping_fallback`)
            if self.stats["sources_failed"]:
                logger.info("\n[PHASE 8] WEB SCRAPING FALLBACK - DISABLED")
                self.run_web_scraping_fallback()
            
            # Log stats
            self.log_stats()
            
            # Short delay before next cycle - minimal for aggressive mode
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
    # Prevent multiple concurrent runners on the VM (easy to accidentally start twice).
    # On Linux, use an advisory file lock; if already locked, exit cleanly.
    try:
        import fcntl  # type: ignore

        lock_f = open("/tmp/mindex_aggressive_runner.lock", "w", encoding="utf-8")
        try:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.error("Another aggressive runner instance is already running. Exiting.")
            return
    except Exception:
        # Non-Linux platforms (or restricted env) just proceed.
        lock_f = None

    runner = AggressiveETLRunner()
    # Run with 2 minute delay between cycles - very aggressive
    runner.run_forever(cycle_delay_minutes=2)


if __name__ == "__main__":
    main()
