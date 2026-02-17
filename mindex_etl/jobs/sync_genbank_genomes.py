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


def _map_sequence_type(molecule_type: Optional[str]) -> str:
    """
    `bio.genetic_sequence.sequence_type` expects a short classifier.
    GenBank moltype strings are often verbose ("genomic DNA", "mRNA", etc).
    """
    mt = (molecule_type or "").strip().lower()
    if "rna" in mt:
        return "rna"
    if "protein" in mt or "aa" in mt:
        return "protein"
    return "dna"


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
                seq_value = genome.get("sequence") or ""
                seq_type = _map_sequence_type(genome.get("molecule_type"))
                species_name = genome.get("organism")
                description = genome.get("definition")
                source_url = genome.get("source_url") or (genome.get("metadata", {}) or {}).get("url")
                
                if existing:
                    # Update existing
                    cur.execute(
                        """
                        UPDATE bio.genetic_sequence SET
                            species_name = %s,
                            gene_name = %s,
                            gene = %s,
                            region = %s,
                            sequence_length = %s,
                            sequence_type = %s,
                            description = %s,
                            source = %s,
                            source_url = %s,
                            sequence = %s,
                            updated_at = now()
                        WHERE accession = %s
                        """,
                        (
                            species_name,
                            "genome",
                            "GENOME",
                            None,
                            genome.get("sequence_length"),
                            seq_type.upper(),
                            description,
                            "GenBank",
                            source_url,
                            seq_value,
                            accession,
                        ),
                    )
                    updated += 1
                else:
                    # Insert new - using bio.genetic_sequence schema
                    cur.execute(
                        """
                        INSERT INTO bio.genetic_sequence (
                            accession, source, species_name, gene_name, gene, region,
                            sequence, sequence_length, sequence_type, description, source_url
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (accession) DO NOTHING
                        """,
                        (
                            accession,
                            "GenBank",
                            species_name,
                            "genome",
                            "GENOME",
                            None,
                            seq_value,
                            genome.get("sequence_length"),
                            seq_type.upper(),
                            description,
                            source_url,
                        ),
                    )
                    inserted += 1
                    
            total = inserted + updated
            if total and total % 200 == 0:
                # This job can run a long time; commit in small batches so results
                # show up immediately and we don't hold one massive transaction.
                conn.commit()

            if total and total % 500 == 0:
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
            seq_value = seq.get("sequence") or ""
                
            with conn.cursor() as cur:
                # Using bio.genetic_sequence schema with gene field
                cur.execute(
                    """
                    INSERT INTO bio.genetic_sequence (
                        accession, source, gene, gene_name, region, species_name,
                        sequence, sequence_length, sequence_type, description, source_url
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (accession) DO NOTHING
                    """,
                    (
                        accession,
                        "GenBank",
                        "ITS",
                        "ITS",
                        seq.get("region"),
                        seq.get("organism"),  # species_name
                        seq_value,
                        seq.get("sequence_length"),
                        _map_sequence_type(seq.get("molecule_type")).upper(),
                        seq.get("definition"),
                        seq.get("source_url") or (seq.get("metadata", {}) or {}).get("url"),
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
                    
            if inserted and inserted % 200 == 0:
                conn.commit()

            if inserted and inserted % 500 == 0:
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
