from __future__ import annotations

import argparse
from typing import Iterable

from ..db import db_session
from ..sources import mycobank
from ..taxon_canonicalizer import link_external_id, upsert_taxon


def _insert_synonym(conn, taxon_id, synonym: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.taxon_synonym (taxon_id, synonym, source)
            SELECT %s, %s, 'mycobank'
            WHERE NOT EXISTS (
                SELECT 1 FROM core.taxon_synonym WHERE taxon_id = %s AND synonym = %s
            )
            """,
            (taxon_id, synonym, taxon_id, synonym),
        )


def sync_mycobank_taxa(*, prefixes: Iterable[str] | None = None) -> int:
    inserted = 0
    with db_session() as conn:
        for taxon_payload, synonyms, external_id in mycobank.iter_mycobank_taxa(prefixes=list(prefixes) if prefixes else None):
            taxon_id = upsert_taxon(conn, **taxon_payload)
            link_external_id(
                conn,
                taxon_id=taxon_id,
                source="mycobank",
                external_id=external_id,
                metadata={"source": "mycobank"},
            )
            for synonym in synonyms:
                if synonym:
                    _insert_synonym(conn, taxon_id, synonym)
            inserted += 1
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync MycoBank taxa into MINDEX")
    parser.add_argument(
        "--prefixes",
        help="Comma separated prefixes to query (default a-z)",
        default=None,
    )
    args = parser.parse_args()
    prefixes = args.prefixes.split(",") if args.prefixes else None
    total = sync_mycobank_taxa(prefixes=prefixes)
    print(f"Processed {total} MycoBank taxa")


if __name__ == "__main__":
    main()
