"""
MINDEX Auto-Enrich Species Job
==============================
Scheduled job that scans species with missing data and triggers enrichment:
- Missing images: fetches from iNaturalist, Wikipedia, GBIF, Mushroom Observer, etc.
- Missing genetics: logs for GenBank sync (bulk job)
- Missing chemistry: logs for PubChem sync (bulk job)

Logs enrichment status per species for monitoring.
Run via scheduler or manually:

    python -m mindex_etl.jobs.auto_enrich_species --limit 100
    python -m mindex_etl.jobs.auto_enrich_species --images-only --limit 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import settings
from ..db import db_session
from ..jobs.species_data_completeness import get_species_completeness
from ..sources.multi_image import MultiSourceImageFetcher, ImageResult


ENRICHMENT_LOG = Path(settings.local_data_dir) / "auto_enrich_species" / "enrichment_log.jsonl"


def _ensure_log_dir() -> Path:
    p = ENRICHMENT_LOG.parent
    p.mkdir(parents=True, exist_ok=True)
    return p


def _log_enrichment(
    taxon_id: str,
    canonical_name: str,
    field: str,
    status: str,
    detail: Optional[str] = None,
) -> None:
    """Append enrichment event to log file."""
    _ensure_log_dir()
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "taxon_id": taxon_id,
        "canonical_name": canonical_name,
        "field": field,
        "status": status,
        "detail": detail,
    }
    with open(ENRICHMENT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _update_taxon_image(taxon_id: str, image: ImageResult, conn) -> bool:
    """Update taxon metadata with default_photo."""
    photo_data = {
        "url": image.url,
        "medium_url": image.medium_url or image.url,
        "original_url": image.original_url or image.medium_url or image.url,
        "source": image.source,
        "attribution": image.photographer or image.attribution,
        "license_code": image.license,
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "quality_score": image.quality_score,
    }
    if image.source_url:
        photo_data["source_url"] = image.source_url

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE core.taxon
            SET
                metadata = jsonb_set(
                    COALESCE(metadata, '{}'::jsonb),
                    '{default_photo}',
                    %s::jsonb,
                    true
                ),
                updated_at = now()
            WHERE id = %s
            """,
            (json.dumps(photo_data), taxon_id),
        )
        return cur.rowcount > 0


async def enrich_species_images(
    limit: int = 100,
    delay_seconds: float = 0.5,
    verbose: bool = True,
) -> Dict[str, int]:
    """
    Enrich species missing images by fetching from multiple sources.
    """
    result = get_species_completeness(limit=limit, incomplete_only=True)
    incomplete = result.get("incomplete", [])
    # Filter to those missing images
    missing_images = [s for s in incomplete if "image" in s.get("missing", [])]
    
    stats: Dict[str, int] = {"enriched": 0, "not_found": 0, "errors": 0}

    if verbose:
        print(f"Auto-enrich: {len(missing_images)} species missing images (limit={limit})")

    async with MultiSourceImageFetcher() as fetcher:
        with db_session() as conn:
            for i, spec in enumerate(missing_images[:limit], 1):
                taxon_id = spec["id"]
                name = spec["canonical_name"]
                try:
                    images = await fetcher.find_images_for_species(name, target_count=8)
                    if images:
                        success = _update_taxon_image(taxon_id, images[0], conn)
                        if success:
                            stats["enriched"] += 1
                            _log_enrichment(
                                taxon_id, name, "image", "ok",
                                f"source={images[0].source} url={images[0].url[:60]}...",
                            )
                            if verbose:
                                print(f"  [{i}] {name}: enriched from {images[0].source}")
                        else:
                            stats["errors"] += 1
                    else:
                        stats["not_found"] += 1
                        _log_enrichment(taxon_id, name, "image", "not_found", None)
                        if verbose:
                            print(f"  [{i}] {name}: no image found")
                except Exception as e:
                    stats["errors"] += 1
                    _log_enrichment(taxon_id, name, "image", "error", str(e))
                    if verbose:
                        print(f"  [{i}] {name}: error {e}")

                await asyncio.sleep(delay_seconds)

    return stats


def run_auto_enrich(
    *,
    limit: int = 100,
    images_only: bool = True,
    delay_seconds: float = 0.5,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Run auto-enrichment for incomplete species.
    
    Args:
        limit: Max species to process
        images_only: Only enrich images (genetics/chemistry require bulk syncs)
        delay_seconds: Delay between API calls
        verbose: Print progress
    
    Returns:
        Dict with stats and any errors
    """
    stats: Dict[str, Any] = {"images": {}, "genetics": "bulk_sync_required", "chemistry": "bulk_sync_required"}

    if images_only:
        img_stats = asyncio.run(
            enrich_species_images(limit=limit, delay_seconds=delay_seconds, verbose=verbose)
        )
        stats["images"] = img_stats

    if verbose and not images_only:
        print("Genetics: run python -m mindex_etl.jobs.sync_genbank_genomes")
        print("Chemistry: run python -m mindex_etl.jobs.sync_pubchem_compounds")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MINDEX Auto-Enrich Species - fill missing images, log genetics/chemistry needs"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max species to process (default: 100)",
    )
    parser.add_argument(
        "--images-only",
        action="store_true",
        default=True,
        help="Only enrich images (default: True)",
    )
    parser.add_argument(
        "--all-fields",
        action="store_true",
        help="Include genetics/chemistry (logs bulk sync recommendations)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between API calls in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose output",
    )

    args = parser.parse_args()
    images_only = args.images_only and not args.all_fields

    result = run_auto_enrich(
        limit=args.limit,
        images_only=images_only,
        delay_seconds=args.delay,
        verbose=not args.quiet,
    )

    if not args.quiet:
        print("\n" + "=" * 60)
        print("AUTO-ENRICH COMPLETE")
        print("=" * 60)
        if result.get("images"):
            im = result["images"]
            print(f"Images enriched: {im.get('enriched', 0)}")
            print(f"Not found:       {im.get('not_found', 0)}")
            print(f"Errors:          {im.get('errors', 0)}")
        print("=" * 60)


if __name__ == "__main__":
    main()
