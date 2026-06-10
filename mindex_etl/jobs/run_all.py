"""
Master ETL Runner for MINDEX
============================
Orchestrates all data source syncs to populate and maintain the biodiversity database.
Domain mode: use --domain-mode all for all-life ingestion, or fungi (default) for fungi-only.

Usage:
    python -m mindex_etl.jobs.run_all --full          # Full initial sync
    python -m mindex_etl.jobs.run_all --incremental   # Incremental update
    python -m mindex_etl.jobs.run_all --source inat   # Sync specific source
"""
from __future__ import annotations

import argparse
import logging
import os
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
    from .sync_mycobank_taxa import sync_mycobank_taxa_compat
    from .sync_gbif_complete import sync_gbif_fungi
    from .sync_fungidb_genomes import sync_fungidb_genomes
    from .backfill_traits import backfill_traits
    from .sync_inat_observations import sync_inat_observations
    from .sync_gbif_occurrences import sync_gbif_occurrences
    from .sync_theyeasts_taxa import sync_theyeasts_taxa
    from .sync_fusarium_taxa import sync_fusarium_taxa
    from .sync_mushroom_world_taxa import sync_mushroom_world_taxa
    from .sync_chemspider_compounds import run_full_sync as chemspider_sync
    from .sync_pubchem_compounds import sync_pubchem_compounds
    from .sync_genbank_genomes import sync_genbank_genomes
    from .ancestry_sync import run_ancestry_sync
    from .backfill_inat_taxon_photos import backfill_inat_taxon_photos
    from .publications import run_publications_etl
    from .hq_media_ingestion import HQMediaIngestionWorker

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
            worker = HQMediaIngestionWorker(limit=max_pages or 100)
            asyncio.run(worker.run())
            stats = worker.checkpoint.stats
            return stats.images_downloaded if stats else 0
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

    def run_pubchem_sync(**kwargs) -> int:
        max_pages = kwargs.get("max_pages")
        try:
            return sync_pubchem_compounds(max_results=max_pages * 50 if max_pages else 500)
        except Exception as e:
            logger.error(f"PubChem sync failed: {e}")
            return 0

    def run_genetics_sync(**kwargs) -> int:
        max_pages = kwargs.get("max_pages")
        try:
            return sync_genbank_genomes(max_pages=max_pages)
        except Exception as e:
            logger.error(f"GenBank genetics sync failed: {e}")
            return 0

    def run_ancestry_job(**kwargs) -> int:
        max_pages = kwargs.get("max_pages")
        enrich_limit = min(max_pages or 50, 200)
        try:
            report = run_ancestry_sync(
                enrich=True,
                enrich_limit=enrich_limit,
                verbose=False,
            )
            stats = report.get("stats") or {}
            enrich_stats = report.get("enrich_stats") or {}
            images = enrich_stats.get("images") or {}
            return int(images.get("enriched", 0) or stats.get("with_images", 0))
        except Exception as e:
            logger.error(f"Ancestry sync failed: {e}")
            return 0

    def run_taxon_photos_sync(**kwargs) -> int:
        limit = kwargs.get("max_pages", 20)
        try:
            return backfill_inat_taxon_photos(limit=(limit or 20) * 50)
        except Exception as e:
            logger.error(f"iNat taxon photo backfill failed: {e}")
            return 0

    def run_gbif_complete_sync(**kwargs) -> int:
        """Incremental pages of full GBIF fungal species dump (not occurrences)."""
        max_pages = kwargs.get("max_pages")
        max_offset = (max_pages * 300) if max_pages else None
        try:
            return sync_gbif_fungi(max_offset=max_offset)
        except Exception as e:
            logger.error(f"GBIF complete fungi sync failed: {e}")
            return 0

    def run_kingdom_backfill(**kwargs) -> int:
        import asyncio
        import os

        from .backfill_kingdom_lineage import run_backfill

        _ = kwargs
        dsn = os.environ.get("MINDEX_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not dsn:
            from ..config import settings

            dsn = settings.database_url
        try:
            asyncio.run(run_backfill(dsn, batch=5000))
            return 0
        except Exception as e:
            logger.error(f"Kingdom/lineage backfill failed: {e}")
            return -1

    registry: Dict[str, ETLJob] = {
        "inat_taxa": ETLJob(
            name="inat_taxa",
            source="iNaturalist",
            run_func=sync_inat_taxa,
            priority=10,
            description="Sync taxonomy from iNaturalist (domain-mode: all or fungi)",
        ),
        "mycobank": ETLJob(
            name="mycobank",
            source="MycoBank",
            run_func=sync_mycobank_taxa_compat,
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
            description="Sync observations with locations and images (domain-mode: all or fungi)",
        ),
        "gbif": ETLJob(
            name="gbif",
            source="GBIF",
            run_func=sync_gbif_occurrences,
            priority=60,
            description="Sync occurrence records from GBIF (domain-mode: all or fungi)",
        ),
        "gbif_complete": ETLJob(
            name="gbif_complete",
            source="GBIF",
            run_func=run_gbif_complete_sync,
            priority=61,
            description="Sync full GBIF fungal species taxonomy (sync_gbif_complete)",
        ),
        "kingdom_backfill": ETLJob(
            name="kingdom_backfill",
            source="MINDEX",
            run_func=run_kingdom_backfill,
            priority=62,
            description="Backfill core.taxon kingdom, lineage, lineage_ids from parent chains",
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
        "pubchem": ETLJob(
            name="pubchem",
            source="PubChem",
            run_func=run_pubchem_sync,
            priority=91,
            description="Sync compounds and molecular metadata from PubChem",
        ),
        "genetics": ETLJob(
            name="genetics",
            source="GenBank",
            run_func=run_genetics_sync,
            priority=92,
            description="Sync genetic sequences (GenBank) into bio.genetic_sequence",
        ),
        "taxon_photos": ETLJob(
            name="taxon_photos",
            source="iNaturalist",
            run_func=run_taxon_photos_sync,
            priority=93,
            description="Backfill default_photo into core.taxon.metadata for ancestry/explorer",
        ),
        "ancestry": ETLJob(
            name="ancestry",
            source="MINDEX",
            run_func=run_ancestry_job,
            priority=94,
            description="Scan species completeness and enrich missing images/descriptions",
        ),
    }

    # ETL container image may not include mindex_api; skip civic job there.
    try:
        from .civic_viewport_sync import sync_civic_viewport_intel

        registry["civic_viewport"] = ETLJob(
            name="civic_viewport",
            source="Civic/Government",
            run_func=sync_civic_viewport_intel,
            priority=95,
            description="Batch-sync civic viewport intelligence (officials, elections, facilities) into civic.*",
        )
    except Exception as exc:
        logger.warning("civic_viewport ETL job unavailable in this runtime: %s", exc)

    def run_nlm_audio_ingest(**kwargs) -> int:
        from .ingest_nlm_audio_p0 import run_ingest

        sources_raw = kwargs.get("sources") or os.environ.get(
            "NLM_AUDIO_SOURCES", "esc50,ds3500,mbari_pacific_sound"
        )
        sources = [s.strip() for s in str(sources_raw).split(",") if s.strip()]
        max_files = int(kwargs.get("max_files_per_source") or 5000)
        max_gb = float(kwargs.get("max_gb") or 200.0)
        return run_ingest(sources, max_files, max_gb)

    registry["nlm_audio_p0"] = ETLJob(
        name="nlm_audio_p0",
        source="NLM_TRAINING_DATA_SOURCES",
        run_func=run_nlm_audio_ingest,
        priority=5,
        description="Download/normalize P0 acoustic corpora to NAS Library + library.blob",
    )

    return registry


def run_etl(
    jobs: Optional[List[str]] = None,
    full_sync: bool = False,
    max_pages: Optional[int] = None,
    domain_mode: Optional[str] = None,
) -> Dict[str, int]:
    """Run ETL jobs and return results."""
    registry = create_job_registry()
    results: Dict[str, int] = {}

    # Select jobs to run
    if jobs:
        job_list = [registry[j] for j in jobs if j in registry]
    else:
        job_list = sorted(registry.values(), key=lambda x: x.priority)

    # Jobs that support domain_mode (all-life vs fungi-only)
    DOMAIN_MODE_JOBS = {"inat_taxa", "inat_obs", "gbif"}

    logger.info(f"Starting ETL run with {len(job_list)} jobs...")
    logger.info(f"Mode: {'FULL SYNC' if full_sync else 'INCREMENTAL'}")
    if domain_mode:
        logger.info(f"Domain mode: {domain_mode} (applies to inat_taxa, inat_obs, gbif)")

    for job in job_list:
        kwargs: Dict[str, object] = {}
        if not full_sync:
            kwargs["max_pages"] = max_pages or 5
        elif max_pages:
            kwargs["max_pages"] = max_pages
        if domain_mode and job.name in DOMAIN_MODE_JOBS:
            kwargs["domain_mode"] = domain_mode

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
    parser.add_argument("--domain-mode", choices=["all", "fungi"], default=None, help="Override domain: 'all' for all-life, 'fungi' for fungi-only (default from config)")
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
        domain_mode=args.domain_mode,
    )

    log_run_summary(results)


if __name__ == "__main__":
    main()
