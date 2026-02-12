"""
Master ETL Runner for MINDEX
============================
Orchestrates all data source syncs to populate and maintain the fungal database.

Usage:
    python -m mindex_etl.jobs.run_all --full          # Full initial sync
    python -m mindex_etl.jobs.run_all --incremental   # Incremental update
    python -m mindex_etl.jobs.run_all --source inat   # Sync specific source
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..db import db_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mindex_etl")


class ETLJob:
    """Represents an ETL job with metadata."""

    def __init__(
        self,
        name: str,
        source: str,
        run_func: Callable,
        priority: int = 100,
        description: str = "",
    ):
        self.name = name
        self.source = source
        self.run_func = run_func
        self.priority = priority
        self.description = description

    def run(self, **kwargs) -> int:
        """Execute the job and return count of processed records."""
        return self.run_func(**kwargs)


def create_job_registry() -> Dict[str, ETLJob]:
    """Create registry of all available ETL jobs."""
    import asyncio
    from .sync_inat_taxa import sync_inat_taxa
    from .sync_mycobank_taxa import sync_mycobank_taxa
    from .sync_fungidb_genomes import sync_fungidb_genomes
    from .backfill_traits import backfill_traits
    from .sync_inat_observations import sync_inat_observations
    from .sync_gbif_occurrences import sync_gbif_occurrences
    from .sync_theyeasts_taxa import sync_theyeasts_taxa
    from .sync_fusarium_taxa import sync_fusarium_taxa
    from .sync_mushroom_world_taxa import sync_mushroom_world_taxa
    from .sync_chemspider_compounds import run_full_sync as chemspider_sync
    from .publications import run_publications_etl
    from .hq_media_ingestion import HQMediaIngestionPipeline
    
    # Wrapper for async publications job
    def run_publications_sync(**kwargs) -> int:
        max_pages = kwargs.get("max_pages", 10)
        try:
            result = asyncio.run(run_publications_etl(max_pubs_per_source=max_pages * 50))
            return result.get("total_publications", 0)
        except Exception as e:
            logger.error(f"Publications sync failed: {e}")
            return 0
    
    # Wrapper for async HQ media job  
    def run_hq_media_sync(**kwargs) -> int:
        max_pages = kwargs.get("max_pages", 100)
        try:
            pipeline = HQMediaIngestionPipeline()
            asyncio.run(pipeline.run(limit=max_pages, sources=None))
            return pipeline.stats.get("total_images", 0) if hasattr(pipeline, "stats") else 0
        except Exception as e:
            logger.error(f"HQ media sync failed: {e}")
            return 0
    
    # Wrapper for chemspider sync
    def run_chemspider_sync(**kwargs) -> int:
        max_pages = kwargs.get("max_pages")
        try:
            result = chemspider_sync(limit=max_pages * 10 if max_pages else None)
            return result.get("total_compounds", 0)
        except Exception as e:
            logger.error(f"ChemSpider sync failed: {e}")
            return 0

    return {
        "inat_taxa": ETLJob(
            name="inat_taxa",
            source="iNaturalist",
            run_func=sync_inat_taxa,
            priority=10,
            description="Sync fungal taxonomy from iNaturalist API (~26,616 species)",
        ),
        "mycobank": ETLJob(
            name="mycobank",
            source="MycoBank",
            run_func=sync_mycobank_taxa,
            priority=15,
            description="Sync taxa and synonyms from MycoBank (~545,007 species)",
        ),
        "theyeasts": ETLJob(
            name="theyeasts",
            source="TheYeasts.org",
            run_func=sync_theyeasts_taxa,
            priority=25,
            description="Sync yeast species from TheYeasts.org (~3,502 species)",
        ),
        "fusarium": ETLJob(
            name="fusarium",
            source="Fusarium.org",
            run_func=sync_fusarium_taxa,
            priority=26,
            description="Sync Fusarium species from Fusarium.org (~408 species)",
        ),
        "mushroom_world": ETLJob(
            name="mushroom_world",
            source="Mushroom.World",
            run_func=sync_mushroom_world_taxa,
            priority=27,
            description="Sync mushroom species from Mushroom.World (~1,000+ species)",
        ),
        "fungidb": ETLJob(
            name="fungidb",
            source="FungiDB",
            run_func=sync_fungidb_genomes,
            priority=30,
            description="Sync genome metadata from FungiDB",
        ),
        "traits": ETLJob(
            name="traits",
            source="Mushroom.World + Wikipedia",
            run_func=backfill_traits,
            priority=40,
            description="Backfill taxon traits from Mushroom.World and Wikipedia",
        ),
        "inat_obs": ETLJob(
            name="inat_obs",
            source="iNaturalist",
            run_func=sync_inat_observations,
            priority=50,
            description="Sync observations with locations and images",
        ),
        "gbif": ETLJob(
            name="gbif",
            source="GBIF",
            run_func=sync_gbif_occurrences,
            priority=60,
            description="Sync occurrence records from GBIF (~50,000+ occurrences)",
        ),
        # Additional jobs from remediation plan
        "hq_media": ETLJob(
            name="hq_media",
            source="iNat/GBIF/Wikipedia",
            run_func=run_hq_media_sync,
            priority=70,
            description="Ingest high-quality fungal images with derivatives",
        ),
        "publications": ETLJob(
            name="publications",
            source="PubMed/GBIF/SemanticScholar",
            run_func=run_publications_sync,
            priority=80,
            description="Sync mycological research publications",
        ),
        "chemspider": ETLJob(
            name="chemspider",
            source="ChemSpider",
            run_func=run_chemspider_sync,
            priority=90,
            description="Sync fungal compound data from ChemSpider",
        ),
    }


def run_etl(
    jobs: Optional[List[str]] = None,
    full_sync: bool = False,
    max_pages: Optional[int] = None,
) -> Dict[str, int]:
    """Run ETL jobs and return results."""
    registry = create_job_registry()
    results: Dict[str, int] = {}

    # Select jobs to run
    if jobs:
        job_list = [registry[j] for j in jobs if j in registry]
    else:
        job_list = sorted(registry.values(), key=lambda x: x.priority)

    # Configure limits for incremental vs full sync
    kwargs = {}
    if not full_sync:
        kwargs["max_pages"] = max_pages or 5  # Small batches for incremental
    elif max_pages:
        kwargs["max_pages"] = max_pages

    logger.info(f"Starting ETL run with {len(job_list)} jobs...")
    logger.info(f"Mode: {'FULL SYNC' if full_sync else 'INCREMENTAL'}")

    for job in job_list:
        logger.info(f"[{job.name}] Starting: {job.description}")
        start_time = time.time()

        try:
            count = job.run(**kwargs)
            elapsed = time.time() - start_time
            results[job.name] = count
            logger.info(f"[{job.name}] Completed: {count} records in {elapsed:.1f}s")
        except Exception as e:
            logger.error(f"[{job.name}] Failed: {e}", exc_info=True)
            results[job.name] = -1

    return results


def log_run_summary(results: Dict[str, int]) -> None:
    """Log a summary of the ETL run."""
    logger.info("=" * 60)
    logger.info("ETL RUN SUMMARY")
    logger.info("=" * 60)

    total_success = 0
    total_failed = 0

    for job_name, count in results.items():
        if count >= 0:
            logger.info(f"  {job_name}: {count:,} records")
            total_success += count
        else:
            logger.error(f"  {job_name}: FAILED")
            total_failed += 1

    logger.info("-" * 60)
    logger.info(f"Total records processed: {total_success:,}")
    if total_failed:
        logger.warning(f"Jobs failed: {total_failed}")
    logger.info("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MINDEX Master ETL Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m mindex_etl.jobs.run_all --full
  python -m mindex_etl.jobs.run_all --incremental --max-pages 10
  python -m mindex_etl.jobs.run_all --jobs inat_taxa mycobank
  python -m mindex_etl.jobs.run_all --list-jobs
        """,
    )
    parser.add_argument("--full", action="store_true", help="Run full sync (no page limits)")
    parser.add_argument("--incremental", action="store_true", help="Run incremental sync (default)")
    parser.add_argument("--max-pages", type=int, default=None, help="Maximum pages to fetch per source")
    parser.add_argument("--jobs", nargs="+", help="Specific jobs to run")
    parser.add_argument("--list-jobs", action="store_true", help="List available jobs and exit")

    args = parser.parse_args()

    if args.list_jobs:
        registry = create_job_registry()
        print("\nAvailable ETL Jobs:")
        print("-" * 60)
        for name, job in sorted(registry.items(), key=lambda x: x[1].priority):
            print(f"  {name:15} [{job.source}] - {job.description}")
        print()
        return

    full_sync = args.full and not args.incremental
    results = run_etl(
        jobs=args.jobs,
        full_sync=full_sync,
        max_pages=args.max_pages,
    )

    log_run_summary(results)


if __name__ == "__main__":
    main()
