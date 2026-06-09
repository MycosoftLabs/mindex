"""
MINDEX Orchestrator entrypoint.
===============================
Single always-on ETL runtime for MINDEX. Supervises one sub-agent per data
source plus the storage-coordination system agents (Supabase sync, AWS backup,
S3 inventory).

Usage::

    python -m mindex_etl.orchestrator                 # run forever (default)
    python -m mindex_etl.orchestrator --once          # one supervised tick
    python -m mindex_etl.orchestrator --list          # list agents, exit
    python -m mindex_etl.orchestrator --no-system     # source agents only
    python -m mindex_etl.orchestrator --concurrency 6 # override parallelism
"""
from __future__ import annotations

import argparse
import logging
import sys

from .agents.orchestrator import MindexOrchestrator


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Reuse the redaction filter from the legacy runner if present.
    try:
        from .aggressive_runner import _RedactHttpxQueryParams

        logging.getLogger("httpx").addFilter(_RedactHttpxQueryParams())
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="MINDEX Agent Orchestrator")
    parser.add_argument("--once", action="store_true", help="Run a single supervised tick and exit")
    parser.add_argument("--list", action="store_true", help="List configured agents and exit")
    parser.add_argument("--no-system", action="store_true", help="Exclude system agents (sync/backup)")
    parser.add_argument("--concurrency", type=int, default=None, help="Max concurrent agents")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Scheduler tick interval")
    args = parser.parse_args()

    _configure_logging()

    orch = MindexOrchestrator(
        max_concurrency=args.concurrency,
        include_system=not args.no_system,
    )

    if args.list:
        # Build the registry without requiring a DB connection.
        from .agents.registry import build_agents

        orch.agents = build_agents(include_system=not args.no_system)
        print(f"\nMINDEX agents ({len(orch.agents)}):")
        print("-" * 78)
        for row in orch.describe():
            mins = row["schedule_seconds"] / 60.0
            print(
                f"  {row['name']:24} [{row['kind']:6}] grp={row['group']:11} "
                f"every {mins:7.1f}m  {row['source']}"
            )
        print()
        return 0

    if args.once:
        orch.run_once()
        return 0

    orch.run_forever(poll_seconds=args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
