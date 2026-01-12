"""
Sync TheYeasts.org Taxa into MINDEX

Syncs ~3,502 yeast species from TheYeasts.org database.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Optional

from ..db import db_session
from ..sources import theyeasts
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
                VALUES (%s, %s, %s, 'theyeasts')
                ON CONFLICT DO NOTHING
                """,
                (taxon_id, trait_name, value_text),
            )
            count += 1
    return count


def sync_theyeasts_taxa(
    *,
    max_pages: Optional[int] = None,
    fetch_details: bool = False,
    delay_seconds: float = 1.0,
) -> dict:
    """
    Sync yeast species from TheYeasts.org into MINDEX.
    
    Args:
        max_pages: Maximum pages to fetch (None for all)
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
    
    print(f"Starting TheYeasts sync at {start_time.isoformat()}", flush=True)
    
    with db_session() as conn:
        try:
            for taxon_payload, source, external_id in theyeasts.iter_theyeasts_species(
                max_pages=max_pages,
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
                            metadata={"source": "theyeasts"},
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
                        conn.commit()  # Commit periodically
                        
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
        "source": "theyeasts",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration,
        "inserted": inserted,
        "updated": updated,
        "traits_added": traits_added,
        "errors": errors,
        "total_processed": inserted + updated,
    }
    
    print(f"\nTheYeasts sync complete:")
    print(f"  Duration: {duration:.1f}s")
    print(f"  Inserted: {inserted}")
    print(f"  Updated: {updated}")
    print(f"  Traits: {traits_added}")
    print(f"  Errors: {errors}")
    
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync TheYeasts.org taxa into MINDEX")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum pages to fetch (default: all)",
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
    
    stats = sync_theyeasts_taxa(
        max_pages=args.max_pages,
        fetch_details=args.fetch_details,
        delay_seconds=args.delay,
    )
    
    print(f"\nSync complete: {stats['total_processed']} taxa processed")


if __name__ == "__main__":
    main()
