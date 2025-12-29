"""
GBIF Occurrences Sync
=====================
Sync fungal occurrence records from GBIF (Global Biodiversity Information Facility).
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

from ..db import db_session
from ..sources import gbif
from ..taxon_canonicalizer import link_external_id, upsert_taxon


def _parse_date(date_str: Optional[str]) -> Optional[str]:
    """Parse and normalize date string for PostgreSQL."""
    if not date_str:
        return None

    # Handle GBIF date ranges like "2025-01-01T00:14/2025-01-01T00:30"
    if "/" in date_str:
        # Take the start of the range
        date_str = date_str.split("/")[0]

    # Handle timestamps that may be incomplete
    try:
        from datetime import datetime
        # Try parsing ISO format
        if "T" in date_str:
            # Normalize incomplete times like "2025-01-01T00:14" to full timestamp
            if len(date_str) == 16:  # YYYY-MM-DDTHH:MM
                date_str += ":00"
            return date_str
        else:
            # Just a date, add time component
            return f"{date_str}T00:00:00"
    except Exception:
        return None


def sync_gbif_occurrences(*, max_pages: Optional[int] = None) -> int:
    """Sync GBIF occurrences into MINDEX database."""
    inserted = 0

    with db_session() as conn:
        # First sync species
        for species in gbif.iter_gbif_species(max_pages=max_pages):
            taxon_id = upsert_taxon(conn, **species)
            gbif_key = species.get("metadata", {}).get("gbif_key")
            if gbif_key:
                link_external_id(
                    conn,
                    taxon_id=taxon_id,
                    source="gbif",
                    external_id=str(gbif_key),
                    metadata={"source": "gbif"},
                )

        # Then sync occurrences
        for obs in gbif.iter_gbif_occurrences(max_pages=max_pages):
            taxon_name = obs.get("taxon_name")
            if not taxon_name:
                continue

            # Upsert taxon
            taxon_id = upsert_taxon(
                conn,
                canonical_name=taxon_name,
                rank=obs.get("taxon_rank", "species"),
                source="gbif",
            )

            # Link GBIF key
            gbif_key = obs.get("taxon_gbif_key")
            if gbif_key:
                link_external_id(
                    conn,
                    taxon_id=taxon_id,
                    source="gbif",
                    external_id=str(gbif_key),
                    metadata={"source": "gbif"},
                )

            # Parse and normalize date
            observed_at = _parse_date(obs.get("observed_at"))

            # Insert observation
            with conn.cursor() as cur:
                location_sql = "NULL"
                location_params = []
                if obs.get("lat") and obs.get("lng"):
                    location_sql = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography"
                    location_params = [obs["lng"], obs["lat"]]

                # Check if exists
                cur.execute(
                    "SELECT 1 FROM obs.observation WHERE source = %s AND source_id = %s",
                    (obs["source"], obs["source_id"]),
                )
                if cur.fetchone():
                    continue  # Already exists

                # Insert with NULL for invalid dates
                insert_sql = f"""
                    INSERT INTO obs.observation (
                        taxon_id, source, source_id, observer, observed_at,
                        location, accuracy_m, media, notes, metadata
                    )
                    VALUES (
                        %s, %s, %s, %s, %s::timestamptz,
                        {location_sql}, %s, %s::jsonb, %s, %s::jsonb
                    )
                """
                cur.execute(
                    insert_sql,
                    (
                        taxon_id,
                        obs["source"],
                        obs["source_id"],
                        obs.get("observer"),
                        observed_at,
                        *location_params,
                        obs.get("accuracy_m"),
                        json.dumps(obs.get("photos", [])),
                        obs.get("notes"),
                        json.dumps(obs.get("metadata", {})),
                    ),
                )
                inserted += 1

    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync GBIF fungal occurrences")
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()

    total = sync_gbif_occurrences(max_pages=args.max_pages)
    print(f"Synced {total} GBIF occurrences")


if __name__ == "__main__":
    main()
