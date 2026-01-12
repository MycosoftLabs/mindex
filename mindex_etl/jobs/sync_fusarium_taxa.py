"""
Sync Fusarium.org Taxa into MINDEX

Syncs ~408 Fusarium species from Fusarium.org database.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Optional

from ..db import db_session
from ..sources import fusarium
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
                VALUES (%s, %s, %s, 'fusarium')
                ON CONFLICT DO NOTHING
                """,
                (taxon_id, trait_name, value_text),
            )
            count += 1
    return count


def sync_fusarium_taxa(
    *,
    fetch_details: bool = False,
    delay_seconds: float = 1.0,
    use_fallback: bool = False,
    max_pages: int | None = None,  # Accept but don't use - for scheduler compatibility
    **kwargs,  # Absorb any extra parameters from scheduler
) -> dict:
    """
    Sync Fusarium species from Fusarium.org into MINDEX.
    
    Args:
        fetch_details: Whether to fetch detailed species pages
        delay_seconds: Delay between requests
        use_fallback: Use fallback species list if scraping fails
    
    Returns:
        dict with sync statistics
    """
    start_time = datetime.utcnow()
    inserted = 0
    updated = 0
    errors = 0
    traits_added = 0
    
    print(f"Starting Fusarium sync at {start_time.isoformat()}", flush=True)
    
    with db_session() as conn:
        try:
            # Choose iterator
            if use_fallback:
                iterator = fusarium.iter_fusarium_fallback()
            else:
                iterator = fusarium.iter_fusarium_species(
                    fetch_details=fetch_details,
                    delay_seconds=delay_seconds,
                )
            
            for taxon_payload, source, external_id in iterator:
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
                            metadata={"source": "fusarium"},
                        )
                        
                        # Add traits
                        if traits:
                            traits_added += _upsert_traits(conn, taxon_id, traits)
                        
                        inserted += 1
                    else:
                        updated += 1
                    
                    # Log progress
                    total = inserted + updated
                    if total % 50 == 0:
                        print(f"Progress: {total} taxa processed ({inserted} new, {updated} updated)", flush=True)
                        conn.commit()
                        
                except Exception as e:
                    print(f"Error processing taxon: {e}")
                    errors += 1
                    continue
            
            conn.commit()
            
        except Exception as e:
            print(f"Sync error: {e}")
            # Try fallback if primary scraping fails
            if not use_fallback:
                print("Attempting fallback list...")
                conn.rollback()
                return sync_fusarium_taxa(
                    fetch_details=False,
                    delay_seconds=delay_seconds,
                    use_fallback=True,
                )
            conn.rollback()
            raise
    
    end_time = datetime.utcnow()
    duration = (end_time - start_time).total_seconds()
    
    stats = {
        "source": "fusarium",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration,
        "inserted": inserted,
        "updated": updated,
        "traits_added": traits_added,
        "errors": errors,
        "total_processed": inserted + updated,
        "used_fallback": use_fallback,
    }
    
    print(f"\nFusarium sync complete:")
    print(f"  Duration: {duration:.1f}s")
    print(f"  Inserted: {inserted}")
    print(f"  Updated: {updated}")
    print(f"  Traits: {traits_added}")
    print(f"  Errors: {errors}")
    if use_fallback:
        print("  (Used fallback species list)")
    
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Fusarium.org taxa into MINDEX")
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
    parser.add_argument(
        "--fallback",
        action="store_true",
        help="Use fallback species list instead of scraping",
    )
    
    args = parser.parse_args()
    
    stats = sync_fusarium_taxa(
        fetch_details=args.fetch_details,
        delay_seconds=args.delay,
        use_fallback=args.fallback,
    )
    
    print(f"\nSync complete: {stats['total_processed']} taxa processed")


if __name__ == "__main__":
    main()
