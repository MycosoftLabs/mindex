"""
Orchestrate MINDEX all-life ETL: GBIF (config domain), iNat (optional jobs), backfill.
Rate-limit aware: long sleeps between major phases. Set MINDEX_KINGDOM_SLICE=Animalia, etc. to
restrict GBIF in future (not implemented — uses config gbif_domain_mode).
"""
from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
import time
from pathlib import Path


def _py() -> list[str]:
    return [sys.executable]


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_phase(name: str, mod: str, main: str = "main") -> int:
    print(f"\n>>> Phase: {name}")
    try:
        m = importlib.import_module(mod)
        fn = getattr(m, main)
        fn()
        return 0
    except Exception as e:
        print(f"ERROR {name}: {e}", file=sys.stderr)
        return 1


def run_subpy_module(mod: str, *args: str) -> int:
    cmd = _py() + ["-m", mod, *args]
    print("RUN:", " ".join(cmd))
    p = subprocess.run(cmd, cwd=str(_root()))
    return p.returncode


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--skip-backfill",
        action="store_true",
        help="Do not run backfill_kingdom_lineage (after migration already applied)",
    )
    p.add_argument(
        "--gbif-only",
        action="store_true",
        help="Only run GBIF all-life sync (smoke)",
    )
    p.add_argument("--max-offset", type=int, default=None, help="Cap GBIF offset for tests")
    args, _rest = p.parse_known_args()
    code = 0
    if (not args.skip_backfill) and (
        os.environ.get("MINDEX_DATABASE_URL") or os.environ.get("DATABASE_URL")
    ):
        r = run_subpy_module("mindex_etl.jobs.backfill_kingdom_lineage")
        if r != 0:
            print("backfill: skipped or failed; continue if migration not yet applied", file=sys.stderr)
    if args.gbif_only:
        r = run_subpy_module("mindex_etl.jobs.sync_gbif_all_life")
        sys.exit(r)
    mo = [str(args.max_offset)] if args.max_offset is not None else []
    r = run_subpy_module("mindex_etl.jobs.sync_gbif_all_life", *([f"--max-offset"] + mo if mo else []))
    code = max(code, r)
    time.sleep(2.0)
    # Optional: invoke sync_inat when job module exists
    try:
        from mindex_etl.jobs import sync_inat_taxa  # type: ignore

        if hasattr(sync_inat_taxa, "main"):
            run_phase("iNaturalist taxa (domain from config)", "mindex_etl.jobs.sync_inat_taxa")
    except Exception as e:
        print(f"inat optional: {e}", file=sys.stderr)
    print("\nrun_all_kingdoms: finished (partial — add more job imports as connectors land)")
    sys.exit(code)


if __name__ == "__main__":
    main()
