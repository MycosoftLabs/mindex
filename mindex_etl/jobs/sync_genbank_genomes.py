"""
GenBank Genome Sync Job
=======================
Sync fungal genome/sequence data from NCBI GenBank into MINDEX.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
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


def _row_value(row, key: str, index: int):
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _resolve_taxon_link(cur, genome: dict) -> tuple[Optional[str], dict]:
    """Resolve only a unique exact NCBI taxonomy crosswalk; names are never used."""
    source_ids = genome.get("source_taxon_ids")
    if source_ids is None:
        source_ids = [genome.get("taxon_id")] if genome.get("taxon_id") is not None else []
    elif isinstance(source_ids, (str, int)):
        source_ids = [source_ids]
    normalized_ids = sorted({str(value).strip() for value in source_ids if str(value).strip()})

    if not normalized_ids:
        return None, {"state": "source_taxon_id_missing", "source": "ncbi", "source_ids": []}
    if len(normalized_ids) != 1 or not normalized_ids[0].isdigit():
        return None, {"state": "source_taxon_id_ambiguous_or_invalid", "source": "ncbi", "source_ids": normalized_ids}

    source_taxon_id = normalized_ids[0]
    cur.execute(
        """
        SELECT DISTINCT taxon_id
        FROM core.taxon_external_id
        WHERE source = %s AND external_id = %s
        LIMIT 2
        """,
        ("ncbi", source_taxon_id),
    )
    matches = cur.fetchall()
    linked_ids = {
        str(value)
        for row in matches
        if (value := _row_value(row, "taxon_id", 0)) is not None
    }
    if len(linked_ids) == 1:
        return next(iter(linked_ids)), {
            "state": "linked_unique_exact_external_id",
            "source": "ncbi",
            "source_ids": [source_taxon_id],
        }
    if len(linked_ids) > 1:
        return None, {
            "state": "ambiguous_exact_external_id",
            "source": "ncbi",
            "source_ids": [source_taxon_id],
            "candidate_count": len(linked_ids),
        }
    return None, {"state": "unlinked_exact_external_id", "source": "ncbi", "source_ids": [source_taxon_id]}


def _genbank_metadata(genome: dict, linkage: dict) -> str:
    metadata = dict(genome.get("metadata") or {})
    metadata["accession_version"] = genome.get("accession_version") or genome.get("accession")
    source_ids = list(linkage.get("source_ids") or [])
    if source_ids:
        metadata["source_taxon_ids"] = source_ids
    metadata["taxon_linkage"] = linkage
    return json.dumps(metadata, sort_keys=True)


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
                    "SELECT id, taxon_id FROM bio.genetic_sequence WHERE accession = %s",
                    (accession,),
                )
                existing = cur.fetchone()
                seq_value = genome.get("sequence") or ""
                seq_type = _map_sequence_type(genome.get("molecule_type"))
                species_name = genome.get("organism")
                description = genome.get("definition")
                accession_version = genome.get("accession_version") or accession
                source_url = (
                    genome.get("source_url")
                    or (genome.get("metadata", {}) or {}).get("url")
                    or f"https://www.ncbi.nlm.nih.gov/nuccore/{accession_version}"
                )
                taxon_id, linkage = _resolve_taxon_link(cur, genome)
                existing_taxon_id = _row_value(existing, "taxon_id", 1) if existing else None
                if taxon_id is None and existing_taxon_id is not None:
                    linkage = {
                        **linkage,
                        "state": "existing_link_preserved_unverified",
                        "preserved_taxon_id": str(existing_taxon_id),
                    }
                metadata = _genbank_metadata(genome, linkage)
                taxonomy = (genome.get("metadata") or {}).get("taxonomy")
                
                if existing:
                    cur.execute(
                        """
                        UPDATE bio.genetic_sequence SET
                            taxon_id = COALESCE(%s, taxon_id),
                            species_name = %s,
                            gene = %s,
                            region = %s,
                            sequence_length = %s,
                            sequence_type = %s,
                            definition = %s,
                            source = %s,
                            source_url = %s,
                            version = %s,
                            sequence = %s,
                            organism = %s,
                            taxonomy = %s,
                            metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb,
                            updated_at = now()
                        WHERE accession = %s
                        """,
                        (
                            taxon_id,
                            species_name,
                            "GENOME",
                            None,
                            genome.get("sequence_length"),
                            seq_type,
                            description,
                            "genbank",
                            source_url,
                            accession_version,
                            seq_value,
                            species_name,
                            taxonomy,
                            metadata,
                            accession,
                        ),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """
                        INSERT INTO bio.genetic_sequence (
                            accession, taxon_id, source, species_name, gene, region,
                            sequence, sequence_length, sequence_type, definition, source_url,
                            version, organism, taxonomy, metadata
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (accession) DO NOTHING
                        """,
                        (
                            accession,
                            taxon_id,
                            "genbank",
                            species_name,
                            "GENOME",
                            None,
                            seq_value,
                            genome.get("sequence_length"),
                            seq_type,
                            description,
                            source_url,
                            accession_version,
                            species_name,
                            taxonomy,
                            metadata,
                        ),
                    )
                    if cur.rowcount > 0:
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
    updated = 0
    
    print(f"Starting GenBank ITS sequence sync (max_pages={max_pages})...")
    
    with db_session() as conn:
        for seq in genbank.iter_fungal_sequences(gene="ITS", limit=100, max_pages=max_pages, delay_seconds=0.5):
            accession = seq.get("accession")
            if not accession:
                continue
            seq_value = seq.get("sequence") or ""
            accession_version = seq.get("accession_version") or accession
            source_url = (
                seq.get("source_url")
                or (seq.get("metadata", {}) or {}).get("url")
                or f"https://www.ncbi.nlm.nih.gov/nuccore/{accession_version}"
            )
                
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, taxon_id FROM bio.genetic_sequence WHERE accession = %s",
                    (accession,),
                )
                existing = cur.fetchone()
                taxon_id, linkage = _resolve_taxon_link(cur, seq)
                existing_taxon_id = _row_value(existing, "taxon_id", 1) if existing else None
                if taxon_id is None and existing_taxon_id is not None:
                    linkage = {
                        **linkage,
                        "state": "existing_link_preserved_unverified",
                        "preserved_taxon_id": str(existing_taxon_id),
                    }
                metadata = _genbank_metadata(seq, linkage)
                taxonomy = (seq.get("metadata") or {}).get("taxonomy")
                cur.execute(
                    """
                    INSERT INTO bio.genetic_sequence (
                        accession, taxon_id, source, gene, region, species_name,
                        sequence, sequence_length, sequence_type, definition, source_url,
                        version, organism, taxonomy, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (accession) DO UPDATE SET
                        taxon_id = COALESCE(EXCLUDED.taxon_id, bio.genetic_sequence.taxon_id),
                        species_name = EXCLUDED.species_name,
                        gene = EXCLUDED.gene,
                        region = EXCLUDED.region,
                        sequence = EXCLUDED.sequence,
                        sequence_length = EXCLUDED.sequence_length,
                        sequence_type = EXCLUDED.sequence_type,
                        definition = EXCLUDED.definition,
                        source_url = EXCLUDED.source_url,
                        version = EXCLUDED.version,
                        organism = EXCLUDED.organism,
                        taxonomy = EXCLUDED.taxonomy,
                        metadata = COALESCE(bio.genetic_sequence.metadata, '{}'::jsonb) || EXCLUDED.metadata,
                        updated_at = now()
                    """,
                    (
                        accession,
                        taxon_id,
                        "genbank",
                        "ITS",
                        seq.get("region"),
                        seq.get("organism"),
                        seq_value,
                        seq.get("sequence_length"),
                        _map_sequence_type(seq.get("molecule_type")),
                        seq.get("definition"),
                        source_url,
                        accession_version,
                        seq.get("organism"),
                        taxonomy,
                        metadata,
                    ),
                )
                if cur.rowcount > 0:
                    if existing:
                        updated += 1
                    else:
                        inserted += 1
                    
            total = inserted + updated
            if total and total % 200 == 0:
                conn.commit()

            if total and total % 500 == 0:
                print(f"GenBank ITS: {inserted} inserted, {updated} updated...", flush=True)
                
    print(f"\nGenBank ITS sync complete: {inserted} inserted, {updated} updated")
    return inserted + updated


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
