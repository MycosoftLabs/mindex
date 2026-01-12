"""
Sync Mushroom.World Taxa into MINDEX

Syncs ~1,000+ mushroom species from Mushroom.World database.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import List, Optional

from ..db import db_session
from ..sources import mushroom_world
from ..taxon_canonicalizer import link_external_id, upsert_taxon


def _upsert_traits(conn, taxon_id: str, traits: list) -> int:
    """Insert trait records for a taxon."""
    count = 0
    for trait in traits:
        trait_name = trait.get("trait_name")
        value_text = trait.get("value_text")
        if not trait_name or not value_text:
            continue
        
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bio.taxon_trait (taxon_id, trait_name, value_text, source)
                VALUES (%s, %s, %s, 'mushroom_world')
                ON CONFLICT DO NOTHING
                """,
                (taxon_id, trait_name, value_text),
            )
            count += 1
    return count


def sync_mushroom_world_taxa(
    *,
    letters: Optional[List[str]] = None,
    max_pages_per_letter: int = 100,
    max_pages: Optional[int] = None,  # Alias for max_pages_per_letter (scheduler compatibility)
    fetch_details: bool = False,
    delay_seconds: float = 1.0,
    **kwargs,  # Absorb any extra parameters from scheduler
) -> dict:
    """
    Sync mushroom species from Mushroom.World into MINDEX.
    
    Args:
        letters: List of letters to sync (a-z), or None for all
        max_pages_per_letter: Maximum pages per letter
        fetch_details: Whether to fetch detailed species pages
        delay_seconds: Delay between requests
    
    Returns:
        dict with sync statistics
    """
    start_time = datetime.utcnow()
    inserted = 0
    updated = 0
    errors = 0
    traits_added = 0
    
    # Use max_pages as alias for max_pages_per_letter if provided
    if max_pages is not None:
        max_pages_per_letter = max_pages
    
    print(f"Starting Mushroom.World sync at {start_time.isoformat()}", flush=True)
    
    with db_session() as conn:
        try:
            for taxon_payload, source, external_id in mushroom_world.iter_mushroom_world_species(
                letters=letters,
                max_pages_per_letter=max_pages_per_letter,
                fetch_details=fetch_details,
                delay_seconds=delay_seconds,
            ):
                try:
                    # Extract traits before upserting taxon
                    traits = taxon_payload.pop("traits", [])
                    
                    # Upsert the taxon
                    taxon_id = upsert_taxon(conn, **taxon_payload)
                    
                    if taxon_id:
                        # Link external ID
                        link_external_id(
                            conn,
                            taxon_id=taxon_id,
                            source=source,
                            external_id=external_id,
                            metadata={"source": "mushroom_world"},
                        )
                        
                        # Add traits
                        if traits:
                            traits_added += _upsert_traits(conn, taxon_id, traits)
                        
                        inserted += 1
                    else:
                        updated += 1
                    
                    # Log progress
                    total = inserted + updated
                    if total % 100 == 0:
                        print(f"Progress: {total} taxa processed ({inserted} new, {updated} updated)", flush=True)
                        conn.commit()
                        
                except Exception as e:
                    print(f"Error processing taxon: {e}")
                    errors += 1
                    continue
            
            conn.commit()
            
        except Exception as e:
            print(f"Sync error: {e}")
            conn.rollback()
            raise
    
    end_time = datetime.utcnow()
    duration = (end_time - start_time).total_seconds()
    
    stats = {
        "source": "mushroom_world",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration,
        "inserted": inserted,
        "updated": updated,
        "traits_added": traits_added,
        "errors": errors,
        "total_processed": inserted + updated,
    }
    
    print(f"\nMushroom.World sync complete:")
    print(f"  Duration: {duration:.1f}s")
    print(f"  Inserted: {inserted}")
    print(f"  Updated: {updated}")
    print(f"  Traits: {traits_added}")
    print(f"  Errors: {errors}")
    
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Mushroom.World taxa into MINDEX")
    parser.add_argument(
        "--letters",
        type=str,
        default=None,
        help="Comma-separated letters to sync (default: all a-z)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Maximum pages per letter (default: 100)",
    )
    parser.add_argument(
        "--fetch-details",
        action="store_true",
        help="Fetch detailed species pages (slower but more complete)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between requests in seconds (default: 1.0)",
    )
    
    args = parser.parse_args()
    
    letters = None
    if args.letters:
        letters = [l.strip().lower() for l in args.letters.split(",")]
    
    stats = sync_mushroom_world_taxa(
        letters=letters,
        max_pages_per_letter=args.max_pages,
        fetch_details=args.fetch_details,
        delay_seconds=args.delay,
    )
    
    print(f"\nSync complete: {stats['total_processed']} taxa processed")


if __name__ == "__main__":
    main()
