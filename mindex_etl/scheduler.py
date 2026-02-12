"""
ETL Scheduler
=============
Continuous background scheduler for running ETL jobs on a schedule.

Usage:
    python -m mindex_etl.scheduler           # Run scheduler daemon
    python -m mindex_etl.scheduler --once    # Run once and exit
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mindex_scheduler")


class ETLScheduler:
    """Simple scheduler for ETL jobs."""

    def __init__(self):
        self.running = True
        self.last_run: Dict[str, datetime] = {}

        # Schedule configuration (job_name -> interval_hours)
        self.schedule = {
            # Core taxonomy sources
            "inat_taxa": 24,           # Daily
            "mycobank": 168,           # Weekly
            "fungidb": 168,            # Weekly
            "traits": 168,             # Weekly
            "inat_obs": 6,             # Every 6 hours
            "gbif": 24,                # Daily
            # Additional data sources (from remediation plan)
            "hq_media": 12,            # Every 12 hours - high quality images
            "publications": 48,        # Every 2 days - research publications
            "chemspider": 168,         # Weekly - chemical compounds
            "genetics": 168,           # Weekly - genetic sequences from GenBank
        }

    def should_run(self, job_name: str) -> bool:
        """Check if a job should run based on its schedule."""
        if job_name not in self.last_run:
            return True

        interval_hours = self.schedule.get(job_name, 24)
        next_run = self.last_run[job_name] + timedelta(hours=interval_hours)
        return datetime.now() >= next_run

    def run_scheduled_jobs(self, max_pages: Optional[int] = 100) -> Dict[str, int]:
        """Run all scheduled jobs that are due."""
        from .jobs.run_all import create_job_registry

        results: Dict[str, int] = {}
        registry = create_job_registry()

        for job_name, job in registry.items():
            if not self.should_run(job_name):
                continue

            logger.info(f"Running scheduled job: {job_name}")
            try:
                count = job.run(max_pages=max_pages)
                results[job_name] = count
                self.last_run[job_name] = datetime.now()
                logger.info(f"Job {job_name} completed: {count} records")
            except Exception as e:
                logger.error(f"Job {job_name} failed: {e}", exc_info=True)
                results[job_name] = -1

        return results

    def run_daemon(self, check_interval_minutes: int = 15):
        """Run scheduler as a daemon process."""

        def signal_handler(signum, frame):
            logger.info("Received shutdown signal, stopping scheduler...")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("Starting ETL scheduler daemon...")
        logger.info(f"Check interval: {check_interval_minutes} minutes")
        logger.info(f"Schedule: {self.schedule}")

        while self.running:
            try:
                results = self.run_scheduled_jobs()
                if results:
                    total = sum(v for v in results.values() if v >= 0)
                    logger.info(f"Scheduler run complete: {total} total records processed")
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)

            # Sleep until next check
            for _ in range(check_interval_minutes * 60):
                if not self.running:
                    break
                time.sleep(1)

        logger.info("Scheduler stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="MINDEX ETL Scheduler")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Check interval in minutes (default: 15)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Max pages per source per run (default: 100)",
    )
    args = parser.parse_args()

    scheduler = ETLScheduler()

    if args.once:
        results = scheduler.run_scheduled_jobs(max_pages=args.max_pages)
        total = sum(v for v in results.values() if v >= 0)
        print(f"Processed {total} records from {len(results)} jobs")
    else:
        scheduler.run_daemon(check_interval_minutes=args.interval)


if __name__ == "__main__":
    main()
