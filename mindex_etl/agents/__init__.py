"""
MINDEX Agent Runtime
====================
A supervised, always-on ETL runtime for MINDEX.

Instead of a single hard-coded loop, MINDEX runs as one **orchestrator agent**
(`MindexOrchestrator`) that supervises many independent **sub-agents**
(`SourceAgent`) — one per data source (GBIF, iNaturalist, GenBank, NOAA,
USGS, ...) plus a handful of *system* agents that keep storage coordinated
(Supabase sync, AWS backup, S3 inventory, storage tiering).

Each sub-agent owns its own schedule, watermark, health, and exponential
backoff, and all of that state is persisted to Postgres (`etl.*`) so the
runtime resumes exactly where it left off after a restart.

Public surface::

    from mindex_etl.agents import MindexOrchestrator, build_agents

    orch = MindexOrchestrator()
    orch.run_forever()          # supervised, continuous
    orch.run_once()             # single supervised tick (for cron/n8n)
"""

from .base import AgentStatus, RunResult, SourceAgent
from .registry import build_agents, build_source_agents, build_system_agents
from .orchestrator import MindexOrchestrator

__all__ = [
    "AgentStatus",
    "RunResult",
    "SourceAgent",
    "MindexOrchestrator",
    "build_agents",
    "build_source_agents",
    "build_system_agents",
]
