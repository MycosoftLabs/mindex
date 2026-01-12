"""
MINDEX Missing Image Backfill Job
==================================
Finds species in the database without images and fetches them from multiple sources.

This job:
1. Queries core.taxon for taxa without default_photo in metadata
2. Prioritizes by observations_count (most popular species first)
3. Searches multiple sources: iNaturalist, Wikipedia, GBIF, Mushroom Observer, Flickr
4. Updates the taxon metadata with the best found image
5. Optionally downloads images to local storage

Run (inside mindex-api container):
    python -m mindex_etl.jobs.backfill_missing_images --limit 1000

Run specific sources only:
    python -m mindex_etl.jobs.backfill_missing_images --sources inat,wikipedia --limit 500

Resume from checkpoint:
    python -m mindex_etl.jobs.backfill_missing_images --resume
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..config import settings
from ..db import db_session
from ..sources.multi_image import MultiSourceImageFetcher, ImageResult


# Checkpoint file for resuming
CHECKPOINT_FILE = Path(settings.local_data_dir) / "backfill_images_checkpoint.json"


def load_checkpoint() -> Dict[str, Any]:
    """Load checkpoint from disk."""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {"processed_ids": [], "stats": {"found": 0, "not_found": 0, "errors": 0}}


def save_checkpoint(data: Dict[str, Any]):
    """Save checkpoint to disk."""
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(data, f)


def get_taxa_missing_images(
    limit: int = 1000,
    offset: int = 0,
    source_filter: Optional[str] = None,
    exclude_ids: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Get taxa that don't have images in their metadata.
    
    Returns taxa ordered by observations_count (most popular first).
    """
    with db_session() as conn:
        with conn.cursor() as cur:
            # Build query
            conditions = [
                "(metadata->>'default_photo') IS NULL",
                "OR (metadata->'default_photo'->>'url') IS NULL",
                "OR (metadata->'default_photo'->>'url') = ''",
            ]
            
            params: List[Any] = []
            
            base_query = """
                SELECT
                    id,
                    canonical_name,
                    rank,
                    source,
                    (metadata->>'inat_id') AS inat_id,
                    (metadata->>'observations_count') AS observations_count,
                    metadata
                FROM core.taxon
                WHERE (
                    (metadata->>'default_photo') IS NULL
                    OR (metadata->'default_photo'->>'url') IS NULL
                    OR (metadata->'default_photo'->>'url') = ''
                )
            """
            
            if source_filter:
                base_query += " AND source = %s"
                params.append(source_filter)
            
            if exclude_ids:
                base_query += f" AND id::text NOT IN ({','.join(['%s'] * len(exclude_ids))})"
                params.extend(exclude_ids)
            
            # Order by popularity
            base_query += """
                ORDER BY
                    CASE
                        WHEN (metadata->>'observations_count') ~ '^[0-9]+$' 
                        THEN (metadata->>'observations_count')::bigint
                        ELSE 0
                    END DESC,
                    canonical_name ASC
                LIMIT %s OFFSET %s
            """
            params.extend([limit, offset])
            
            cur.execute(base_query, params)
            rows = cur.fetchall()
            
    return [dict(row) for row in rows]


def get_total_missing_count(source_filter: Optional[str] = None) -> int:
    """Get total count of taxa missing images."""
    with db_session() as conn:
        with conn.cursor() as cur:
            query = """
                SELECT COUNT(*) as count
                FROM core.taxon
                WHERE (
                    (metadata->>'default_photo') IS NULL
                    OR (metadata->'default_photo'->>'url') IS NULL
                    OR (metadata->'default_photo'->>'url') = ''
                )
            """
            params = []
            if source_filter:
                query += " AND source = %s"
                params.append(source_filter)
            
            cur.execute(query, params)
            row = cur.fetchone()
            return row["count"] if row else 0


def update_taxon_image(
    taxon_id: str,
    image_result: ImageResult,
    conn=None,
) -> bool:
    """
    Update a taxon's metadata with the found image.
    
    Stores the image in the same format as iNaturalist default_photo:
    {
        "url": "...",
        "medium_url": "...",
        "original_url": "...",
        "source": "...",
        "attribution": "...",
        "scraped_at": "..."
    }
    """
    photo_data = {
        "url": image_result.url,
        "medium_url": image_result.medium_url or image_result.url,
        "original_url": image_result.original_url or image_result.url,
        "source": image_result.source,
        "attribution": image_result.photographer or image_result.attribution,
        "license_code": image_result.license,
        "scraped_at": datetime.now().isoformat(),
        "quality_score": image_result.quality_score,
    }
    
    # Add source URL if available
    if image_result.source_url:
        photo_data["source_url"] = image_result.source_url
    
    close_conn = False
    if conn is None:
        conn = db_session().__enter__()
        close_conn = True
    
    try:
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
                    updated_at = NOW()
                WHERE id = %s
                """,
                (json.dumps(photo_data), taxon_id),
            )
        
        if close_conn:
            conn.commit()
        
        return True
        
    except Exception as e:
        print(f"Error updating taxon {taxon_id}: {e}")
        if close_conn:
            conn.rollback()
        return False
    finally:
        if close_conn:
            conn.close()


async def backfill_missing_images(
    *,
    limit: int = 1000,
    sources: Optional[List[str]] = None,
    source_filter: Optional[str] = None,
    delay_seconds: float = 0.5,
    batch_size: int = 50,
    resume: bool = False,
    download_images: bool = False,
    verbose: bool = True,
) -> Dict[str, int]:
    """
    Main backfill function - finds and fills missing images.
    
    Args:
        limit: Maximum number of taxa to process
        sources: Image sources to query (default: all)
        source_filter: Only process taxa from this source (e.g., 'inat', 'gbif')
        delay_seconds: Delay between taxa
        batch_size: Commit batch size
        resume: Resume from checkpoint
        download_images: Whether to download images to local storage
        verbose: Print progress
    
    Returns:
        Dict with stats: found, not_found, errors
    """
    # Load checkpoint if resuming
    checkpoint = load_checkpoint() if resume else {"processed_ids": [], "stats": {"found": 0, "not_found": 0, "errors": 0}}
    processed_ids = set(checkpoint.get("processed_ids", []))
    stats = checkpoint.get("stats", {"found": 0, "not_found": 0, "errors": 0})
    
    # Get total count
    total_missing = get_total_missing_count(source_filter)
    if verbose:
        print(f"\n{'='*60}")
        print(f"MINDEX Missing Image Backfill")
        print(f"{'='*60}")
        print(f"Total taxa missing images: {total_missing}")
        print(f"Processing limit: {limit}")
        print(f"Sources: {', '.join(sources) if sources else 'all'}")
        if resume:
            print(f"Resuming from checkpoint ({len(processed_ids)} already processed)")
        print(f"{'='*60}\n")
    
    # Get taxa to process
    taxa = get_taxa_missing_images(
        limit=limit,
        source_filter=source_filter,
        exclude_ids=list(processed_ids) if processed_ids else None,
    )
    
    if not taxa:
        if verbose:
            print("No taxa to process!")
        return stats
    
    if verbose:
        print(f"Found {len(taxa)} taxa to process\n")
    
    # Process taxa
    async with MultiSourceImageFetcher() as fetcher:
        with db_session() as conn:
            batch_count = 0
            
            for i, taxon in enumerate(taxa, 1):
                taxon_id = str(taxon["id"])
                canonical_name = taxon["canonical_name"]
                obs_count = taxon.get("observations_count") or 0
                
                try:
                    if verbose:
                        print(f"[{i}/{len(taxa)}] {canonical_name} (obs: {obs_count})...", end=" ", flush=True)
                    
                    # Search for images
                    image = await fetcher.find_best_image(canonical_name, sources)
                    
                    if image:
                        # Update the database
                        success = update_taxon_image(taxon_id, image, conn)
                        
                        if success:
                            stats["found"] += 1
                            if verbose:
                                print(f"✓ [{image.source}] {image.url[:60]}...")
                        else:
                            stats["errors"] += 1
                            if verbose:
                                print("✗ DB update failed")
                    else:
                        stats["not_found"] += 1
                        if verbose:
                            print("✗ No image found")
                    
                    # Track processed
                    processed_ids.add(taxon_id)
                    batch_count += 1
                    
                    # Commit batch
                    if batch_count >= batch_size:
                        conn.commit()
                        # Save checkpoint
                        save_checkpoint({
                            "processed_ids": list(processed_ids),
                            "stats": stats,
                            "last_update": datetime.now().isoformat(),
                        })
                        batch_count = 0
                    
                    # Rate limit
                    await asyncio.sleep(delay_seconds)
                    
                except Exception as e:
                    stats["errors"] += 1
                    if verbose:
                        print(f"✗ Error: {e}")
                    continue
            
            # Final commit
            conn.commit()
            save_checkpoint({
                "processed_ids": list(processed_ids),
                "stats": stats,
                "last_update": datetime.now().isoformat(),
                "completed": True,
            })
    
    # Print summary
    if verbose:
        print(f"\n{'='*60}")
        print("BACKFILL COMPLETE")
        print(f"{'='*60}")
        print(f"Images found:     {stats['found']}")
        print(f"Not found:        {stats['not_found']}")
        print(f"Errors:           {stats['errors']}")
        print(f"Success rate:     {stats['found'] / (stats['found'] + stats['not_found'] + stats['errors']) * 100:.1f}%")
        print(f"{'='*60}\n")
    
    return stats


def run_backfill_sync(
    limit: int = 1000,
    sources: Optional[List[str]] = None,
    source_filter: Optional[str] = None,
    resume: bool = False,
) -> Dict[str, int]:
    """Synchronous wrapper for backfill."""
    return asyncio.run(
        backfill_missing_images(
            limit=limit,
            sources=sources,
            source_filter=source_filter,
            resume=resume,
        )
    )


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Backfill missing images for MINDEX taxa from multiple sources"
    )
    parser.add_argument(
        "--limit", 
        type=int, 
        default=1000,
        help="Maximum number of taxa to process (default: 1000)"
    )
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help="Comma-separated list of sources (inat,wikipedia,gbif,mushroom_observer,flickr,bing)"
    )
    parser.add_argument(
        "--source-filter",
        type=str,
        default=None,
        help="Only process taxa from this source (e.g., inat, gbif)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between taxa in seconds (default: 0.5)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Database commit batch size (default: 50)"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose output"
    )
    parser.add_argument(
        "--clear-checkpoint",
        action="store_true",
        help="Clear the checkpoint file and start fresh"
    )
    
    args = parser.parse_args()
    
    # Clear checkpoint if requested
    if args.clear_checkpoint and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        print("Checkpoint cleared.")
    
    # Parse sources
    sources = None
    if args.sources:
        sources = [s.strip() for s in args.sources.split(",")]
    
    # Run backfill
    asyncio.run(
        backfill_missing_images(
            limit=args.limit,
            sources=sources,
            source_filter=args.source_filter,
            delay_seconds=args.delay,
            batch_size=args.batch_size,
            resume=args.resume,
            verbose=not args.quiet,
        )
    )


if __name__ == "__main__":
    main()
