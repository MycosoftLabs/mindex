"""
GenBank Genome Sync Job
=======================
Sync fungal genome/sequence data from NCBI GenBank into MINDEX.
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

from ..db import db_session
from ..sources import genbank


def sync_genbank_genomes(*, max_pages: Optional[int] = None) -> int:
    """Sync GenBank fungal genome records into MINDEX database."""
    inserted = 0
    updated = 0
    
    print(f"Starting GenBank genome sync (max_pages={max_pages})...")
    
    with db_session() as conn:
        for genome in genbank.iter_fungal_genomes(limit=100, max_pages=max_pages, delay_seconds=0.5):
            accession = genome.get("accession")
            if not accession:
                continue
                
            with conn.cursor() as cur:
                # Using bio.genetic_sequence schema
                cur.execute(
                    "SELECT id FROM bio.genetic_sequence WHERE accession = %s",
                    (accession,),
                )
                existing = cur.fetchone()
                
                if existing:
                    # Update existing
                    cur.execute(
                        """
                        UPDATE bio.genetic_sequence SET
                            organism = %s,
                            species_name = %s,
                            sequence_length = %s,
                            sequence_type = %s,
                            definition = %s,
                            taxonomy = %s,
                            metadata = %s::jsonb,
                            updated_at = now()
                        WHERE accession = %s
                        """,
                        (
                            genome.get("organism"),
                            genome.get("organism"),  # species_name same as organism
                            genome.get("sequence_length"),
                            genome.get("molecule_type") or "dna",
                            genome.get("definition"),
                            genome.get("metadata", {}).get("taxonomy"),
                            json.dumps(genome.get("metadata", {})),
                            accession,
                        ),
                    )
                    updated += 1
                else:
                    # Insert new - using bio.genetic_sequence schema
                    cur.execute(
                        """
                        INSERT INTO bio.genetic_sequence (
                            accession, source, organism, species_name,
                            sequence_length, sequence_type, definition, taxonomy, metadata,
                            sequence
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                        ON CONFLICT (accession) DO NOTHING
                        """,
                        (
                            accession,
                            "genbank",
                            genome.get("organism"),
                            genome.get("organism"),  # species_name
                            genome.get("sequence_length"),
                            genome.get("molecule_type") or "dna",
                            genome.get("definition"),
                            genome.get("metadata", {}).get("taxonomy"),
                            json.dumps(genome.get("metadata", {})),
                            "",  # Empty sequence - we'd need to fetch full sequence separately
                        ),
                    )
                    inserted += 1
                    
            if (inserted + updated) % 500 == 0:
                print(f"GenBank: {inserted} inserted, {updated} updated...", flush=True)
                
    print(f"\nGenBank genome sync complete:")
    print(f"  Inserted: {inserted}")
    print(f"  Updated: {updated}")
    
    return inserted + updated


def sync_genbank_its_sequences(*, max_pages: Optional[int] = None) -> int:
    """Sync ITS (fungal barcode) sequences from GenBank."""
    inserted = 0
    
    print(f"Starting GenBank ITS sequence sync (max_pages={max_pages})...")
    
    with db_session() as conn:
        for seq in genbank.iter_fungal_sequences(gene="ITS", limit=100, max_pages=max_pages, delay_seconds=0.5):
            accession = seq.get("accession")
            if not accession:
                continue
                
            with conn.cursor() as cur:
                # Using bio.genetic_sequence schema with gene field
                cur.execute(
                    """
                    INSERT INTO bio.genetic_sequence (
                        accession, source, gene, organism, species_name,
                        sequence_length, definition, metadata, sequence
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (accession) DO NOTHING
                    """,
                    (
                        accession,
                        "genbank",
                        "ITS",
                        seq.get("organism"),
                        seq.get("organism"),  # species_name
                        seq.get("sequence_length"),
                        seq.get("definition"),
                        json.dumps(seq.get("metadata", {})),
                        "",  # Empty sequence placeholder
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
                    
            if inserted % 500 == 0:
                print(f"GenBank ITS: {inserted} inserted...", flush=True)
                
    print(f"\nGenBank ITS sync complete: {inserted} sequences")
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync GenBank fungal genomes")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--its-only", action="store_true", help="Only sync ITS sequences")
    args = parser.parse_args()

    if args.its_only:
        total = sync_genbank_its_sequences(max_pages=args.max_pages)
    else:
        total = sync_genbank_genomes(max_pages=args.max_pages)
        
    print(f"Synced {total} GenBank records")


if __name__ == "__main__":
    main()
