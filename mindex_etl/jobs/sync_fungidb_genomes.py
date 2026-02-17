from __future__ import annotations

import argparse
import json

from ..db import db_session
from ..sources import fungidb
from ..taxon_canonicalizer import upsert_taxon


def sync_fungidb_genomes(*, max_pages: int | None = None) -> int:
    inserted = 0
    with db_session() as conn:
        for record in fungidb.iter_fungidb_genomes(max_pages=max_pages):
            taxon_name = record.get("taxon_name")
            if not taxon_name:
                continue
            taxon_id = upsert_taxon(
                conn,
                canonical_name=taxon_name,
                rank="species",
                source="fungidb",
            )
            accession = record.get("accession")
            if not accession:
                continue

            # Avoid relying on a unique constraint existing on the target database.
            # Some deployments may not have the expected `uq_genome_source_accession` index.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM bio.genome WHERE source = 'fungidb' AND accession = %s",
                    (accession,),
                )
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        """
                        UPDATE bio.genome SET
                            taxon_id = %s,
                            assembly_level = %s,
                            release_date = %s::date,
                            metadata = %s::jsonb
                        WHERE source = 'fungidb' AND accession = %s
                        """,
                        (
                            taxon_id,
                            record.get("assembly_level"),
                            record.get("release_date"),
                            json.dumps(record.get("metadata", {})),
                            accession,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO bio.genome (
                            taxon_id,
                            source,
                            accession,
                            assembly_level,
                            release_date,
                            metadata
                        )
                        VALUES (%s, 'fungidb', %s, %s, %s::date, %s::jsonb)
                        """,
                        (
                            taxon_id,
                            accession,
                            record.get("assembly_level"),
                            record.get("release_date"),
                            json.dumps(record.get("metadata", {})),
                        ),
                    )
                    inserted += 1
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync FungiDB genome metadata")
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()
    total = sync_fungidb_genomes(max_pages=args.max_pages)
    print(f"Processed {total} genome records")


if __name__ == "__main__":
    main()
