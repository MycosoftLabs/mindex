"""
MINDEX Ancestry Sync Job
========================
Ensures every species in MINDEX appears in Ancestry with adequate data.
- Scans species (rank=species) for completeness
- Reports missing/incomplete species
- Optionally triggers auto_enrich_species for incomplete taxa
- Runs on schedule (daily) or manually

Run:
    python -m mindex_etl.jobs.ancestry_sync
    python -m mindex_etl.jobs.ancestry_sync --enrich --limit 50
    python -m mindex_etl.jobs.ancestry_sync --json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import os

from ..config import settings
from ..jobs.species_data_completeness import get_species_completeness
from .auto_enrich_species import run_auto_enrich


SYNC_REPORT_DIR = Path(settings.local_data_dir) / "ancestry_sync"
# Queue file path - shared with mindex_api for viewed-incomplete logging
_QUEUE_FILE = os.getenv("MINDEX_ENRICHMENT_QUEUE_FILE")
VIEWED_INCOMPLETE_FILE = Path(_QUEUE_FILE) if _QUEUE_FILE else SYNC_REPORT_DIR / "viewed_incomplete.jsonl"


def _ensure_report_dir() -> Path:
    SYNC_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return SYNC_REPORT_DIR


def load_viewed_incomplete() -> List[Dict[str, Any]]:
    """Load taxa IDs that were viewed while incomplete (queue for prioritization)."""
    # Use same path as mindex_api enrichment_queue
    path = os.getenv("MINDEX_ENRICHMENT_QUEUE_FILE")
    queue_path = Path(path) if path else SYNC_REPORT_DIR / "viewed_incomplete.jsonl"
    if not queue_path.exists():
        return []
    items = []
    try:
        with open(queue_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return items


def run_ancestry_sync(
    *,
    limit: Optional[int] = None,
    enrich: bool = False,
    enrich_limit: int = 50,
    rank_filter: str = "species",
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Run ancestry sync: scan species, report completeness, optionally enrich.

    Returns:
        Dict with total, with_images, with_description, with_genetics,
        incomplete_count, incomplete (sample), viewed_incomplete (prioritized),
        and optionally enrich_stats.
    """
    result = get_species_completeness(
        limit=limit,
        incomplete_only=False,
        rank_filter=rank_filter,
    )

    viewed = load_viewed_incomplete()
    viewed_ids = {str(v["taxon_id"]) for v in viewed}
    incomplete = result.get("incomplete", [])

    # Prioritize incomplete species that were recently viewed
    prioritized = [s for s in incomplete if s["id"] in viewed_ids]

    report = {
        "scanned_at": datetime.utcnow().isoformat() + "Z",
        "total": result["total"],
        "with_images": result["with_images"],
        "with_description": result["with_description"],
        "with_genetics": result["with_genetics"],
        "incomplete_count": result["incomplete_count"],
        "incomplete_sample": incomplete[:100],
        "viewed_incomplete_count": len(prioritized),
        "viewed_incomplete_sample": prioritized[:20],
        "stats": result.get("stats", {}),
    }

    if enrich and result["incomplete_count"] > 0:
        enrich_stats = run_auto_enrich(
            limit=enrich_limit,
            images_only=True,
            delay_seconds=0.5,
            verbose=verbose,
        )
        report["enrich_stats"] = enrich_stats

    # Save report
    _ensure_report_dir()
    report_path = SYNC_REPORT_DIR / f"sync_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    if verbose:
        s = report["stats"]
        print("\n" + "=" * 60)
        print("ANCESTRY SYNC COMPLETE")
        print("=" * 60)
        print(f"Total species:         {s.get('total_species', 0):,}")
        print(f"With images:           {s.get('with_images', 0):,}")
        print(f"With description:      {s.get('with_description', 0):,}")
        print(f"With genetics:         {s.get('with_genetics', 0):,}")
        print(f"Incomplete:            {report['incomplete_count']:,}")
        print(f"Viewed (prioritized):  {report['viewed_incomplete_count']:,}")
        print("=" * 60)
        if report.get("enrich_stats"):
            im = report["enrich_stats"].get("images", {})
            print(f"Enriched images:       {im.get('enriched', 0)}")
            print(f"Not found:             {im.get('not_found', 0)}")
            print(f"Errors:                {im.get('errors', 0)}")
        print("=" * 60)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MINDEX Ancestry Sync - scan species completeness, optionally enrich"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit incomplete species list (default: no limit)",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Trigger auto_enrich for incomplete species",
    )
    parser.add_argument(
        "--enrich-limit",
        type=int,
        default=50,
        help="Max species to enrich (default: 50)",
    )
    parser.add_argument(
        "--rank",
        type=str,
        default="species",
        help="Taxon rank to scan (default: species)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON to stdout",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose output",
    )

    args = parser.parse_args()

    report = run_ancestry_sync(
        limit=args.limit,
        enrich=args.enrich,
        enrich_limit=args.enrich_limit,
        rank_filter=args.rank,
        verbose=not args.quiet,
    )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
