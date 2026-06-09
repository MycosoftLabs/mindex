"""
Agent registry — derive sub-agents from existing MINDEX jobs.
=============================================================
We do **not** rewrite the ~60 source connectors or ~20 jobs. Instead we wrap the
existing ``mindex_etl.jobs.run_all`` registry (and the earth-data sync
orchestrator) into ``SourceAgent`` objects, then add a few *system* agents that
keep storage coordinated (Supabase sync, AWS backup, S3 inventory).

Each agent inherits a sensible schedule from the legacy
``mindex_etl.scheduler.ETLScheduler`` policy and a concurrency group chosen so
we never hammer the same upstream host (or the same backup target) in parallel.

Building the registry is defensive: if a particular job module fails to import
in a given runtime image, that single agent is skipped with a warning instead
of taking down the whole runtime.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Dict, List, Optional

from .base import SourceAgent

logger = logging.getLogger("mindex.agents.registry")

# Concurrency group per agent — agents in the same group never run concurrently,
# which keeps us from tripping per-host rate limits (NCBI, RSC, GBIF, ...).
CONCURRENCY_GROUPS: Dict[str, str] = {
    "inat_taxa": "inaturalist",
    "inat_obs": "inaturalist",
    "taxon_photos": "inaturalist",
    "gbif": "gbif",
    "mycobank": "mycobank",
    "fungidb": "fungidb",
    "theyeasts": "webscrape",
    "fusarium": "webscrape",
    "mushroom_world": "webscrape",
    "traits": "webscrape",
    "genetics": "ncbi",
    "pubchem": "ncbi",
    "publications": "literature",
    "chemspider": "rsc",
    "hq_media": "media",
    "nlm_audio_p0": "media",
    "ancestry": "internal",
    "civic_viewport": "civic",
    "earth_realtime": "earth",
    "earth_catalog": "earth",
    # system agents serialize on one group so backups never overlap
    "supabase_sync": "sync",
    "aws_backup_pg": "backup",
    "aws_backup_nas_manifest": "backup",
    "s3_inventory": "backup",
}

# Per-agent schedule overrides (seconds). Anything not listed falls back to the
# legacy ETLScheduler hour policy, then to 24h.
SCHEDULE_OVERRIDES: Dict[str, int] = {
    "inat_obs": 300,          # 5 min — live map freshness
    "earth_realtime": 900,    # 15 min — quakes/fires/aircraft/weather
    "earth_catalog": 86_400,  # daily — satellites/launches/cables/species
    "supabase_sync": 300,     # 5 min — push hot rows to the cloud mirror
    "aws_backup_pg": 86_400,  # daily pg_dump → S3
    "aws_backup_nas_manifest": 86_400,
    "s3_inventory": 86_400,
}

# Jobs that understand the all-life vs fungi-only selector.
DOMAIN_MODE_AGENTS = {"inat_taxa", "inat_obs", "gbif"}


def _default_max_pages() -> int:
    try:
        return max(1, int(os.getenv("MINDEX_AGENT_MAX_PAGES", "25")))
    except ValueError:
        return 25


def _domain_mode() -> str:
    return (
        os.getenv("MINDEX_DOMAIN_MODE")
        or os.getenv("INAT_DOMAIN_MODE")
        or os.getenv("GBIF_DOMAIN_MODE")
        or "all"
    ).strip().lower()


def _legacy_schedule_seconds() -> Dict[str, int]:
    """Read the proven hour-based schedule from the legacy scheduler."""
    out: Dict[str, int] = {}
    try:
        from ..scheduler import ETLScheduler

        for name, hours in ETLScheduler().schedule.items():
            out[name] = max(60, int(float(hours) * 3600))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read legacy schedule: %s", exc)
    return out


def build_source_agents() -> List[SourceAgent]:
    """Wrap every job in ``run_all.create_job_registry`` as a SourceAgent."""
    agents: List[SourceAgent] = []
    legacy = _legacy_schedule_seconds()
    default_pages = _default_max_pages()
    domain_mode = _domain_mode()

    try:
        from ..jobs.run_all import create_job_registry

        registry = create_job_registry()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not build job registry (source agents disabled): %s", exc)
        registry = {}

    for name, job in registry.items():
        schedule = SCHEDULE_OVERRIDES.get(name) or legacy.get(name) or 86_400
        agent = SourceAgent(
            name=name,
            run_func=job.run,
            source=getattr(job, "source", ""),
            kind="source",
            description=getattr(job, "description", ""),
            priority=getattr(job, "priority", 100),
            schedule_seconds=schedule,
            concurrency_group=CONCURRENCY_GROUPS.get(name, name),
            max_pages=default_pages,
            domain_mode=domain_mode if name in DOMAIN_MODE_AGENTS else None,
        )
        # iNat observations: keep MINDEX (not the website) hydrating the live map
        # with a rolling overlap window — mirrors the legacy scheduler.
        if name == "inat_obs":
            agent.extra_kwargs = {
                "per_page": 50,
                "lookback_hours": 24,
                "backfill_records": 1000,
            }
        agents.append(agent)

    # Earth-scale real-time + catalog syncs (separate orchestrator, no max_pages).
    try:
        from ..jobs.sync_earth_data import run_catalog_sync, run_realtime_sync

        agents.append(
            SourceAgent(
                name="earth_realtime",
                run_func=lambda **_: _earth_total(run_realtime_sync()),
                source="USGS/NASA/NOAA/OpenAQ",
                kind="source",
                description="Real-time planetary feeds: earthquakes, wildfires, solar, air quality",
                priority=55,
                schedule_seconds=SCHEDULE_OVERRIDES["earth_realtime"],
                concurrency_group="earth",
            )
        )
        agents.append(
            SourceAgent(
                name="earth_catalog",
                run_func=lambda **_: _earth_total(run_catalog_sync()),
                source="CelesTrak/LaunchLibrary/GBIF",
                kind="source",
                description="Daily catalogs: satellites, launches, submarine cables, all-kingdom species",
                priority=58,
                schedule_seconds=SCHEDULE_OVERRIDES["earth_catalog"],
                concurrency_group="earth",
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Earth-data agents unavailable: %s", exc)

    return agents


def _earth_total(results) -> int:
    """run_realtime_sync/run_catalog_sync return {name: count|err}; sum the ints."""
    if not isinstance(results, dict):
        return 0
    total = 0
    for value in results.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += int(value)
    return total


def build_system_agents() -> List[SourceAgent]:
    """Storage-coordination agents: Supabase sync, AWS backups, S3 inventory.

    Each wrapper imports its heavy/optional dependency lazily so a missing
    package (e.g. boto3) only disables that one agent's runs, never the
    registry build.
    """
    agents: List[SourceAgent] = []

    def _run_supabase_sync(**_) -> int:
        from ..sync.supabase_sync import SupabaseSyncWorker

        return SupabaseSyncWorker().run_once()

    def _run_pg_backup(**_) -> int:
        from ..backup.aws_backup import pg_dump_to_s3

        report = pg_dump_to_s3()
        return 1 if report.get("status") == "success" else 0

    def _run_nas_manifest(**_) -> int:
        from ..backup.aws_backup import sync_nas_manifest_to_s3

        report = sync_nas_manifest_to_s3()
        return int(report.get("object_count") or 0)

    def _run_s3_inventory(**_) -> int:
        from ..jobs.s3_collector import collect_s3_inventory

        return int(collect_s3_inventory() or 0)

    agents.append(
        SourceAgent(
            name="supabase_sync",
            run_func=_run_supabase_sync,
            source="Supabase",
            kind="system",
            description="Push new/updated hot rows from Postgres to the Supabase cloud mirror",
            priority=20,
            schedule_seconds=SCHEDULE_OVERRIDES["supabase_sync"],
            concurrency_group="sync",
        )
    )
    agents.append(
        SourceAgent(
            name="aws_backup_pg",
            run_func=_run_pg_backup,
            source="AWS S3",
            kind="system",
            description="Nightly pg_dump of the canonical DB to S3 (Standard-IA, lifecycle to Glacier)",
            priority=30,
            schedule_seconds=SCHEDULE_OVERRIDES["aws_backup_pg"],
            concurrency_group="backup",
            # Backups failing shouldn't spin fast; back off for hours.
            error_backoff_base=1800,
        )
    )
    agents.append(
        SourceAgent(
            name="aws_backup_nas_manifest",
            run_func=_run_nas_manifest,
            source="AWS S3",
            kind="system",
            description="Snapshot the NAS file manifest to S3 and tier cold dirs to Glacier Deep Archive",
            priority=31,
            schedule_seconds=SCHEDULE_OVERRIDES["aws_backup_nas_manifest"],
            concurrency_group="backup",
            error_backoff_base=1800,
        )
    )
    agents.append(
        SourceAgent(
            name="s3_inventory",
            run_func=_run_s3_inventory,
            source="AWS S3",
            kind="system",
            description="Inventory the S3 cold bucket into network.storage_node for federation",
            priority=32,
            schedule_seconds=SCHEDULE_OVERRIDES["s3_inventory"],
            concurrency_group="backup",
            error_backoff_base=1800,
        )
    )
    return agents


def build_agents(include_system: bool = True) -> Dict[str, SourceAgent]:
    """Full agent registry: every source agent + system agents, keyed by name."""
    agents = build_source_agents()
    if include_system:
        agents.extend(build_system_agents())
    out: Dict[str, SourceAgent] = {}
    for agent in agents:
        out[agent.name] = agent
    logger.info(
        "Built %d agents (%d source, %d system)",
        len(out),
        sum(1 for a in out.values() if a.kind == "source"),
        sum(1 for a in out.values() if a.kind == "system"),
    )
    return out
