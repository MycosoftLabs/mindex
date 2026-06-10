#!/usr/bin/env python3
"""Set content_hash on core.taxon rows missing hashes (integrity summary)."""
from __future__ import annotations

import argparse

from mindex_etl.db import db_session


def backfill_taxa(*, limit: int = 10000) -> int:
    with db_session() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE core.taxon
                SET content_hash = digest(
                    coalesce(canonical_name, '') || '|' || coalesce(rank, '') || '|' || coalesce(source, ''),
                    'sha256'
                )
                WHERE content_hash IS NULL
                AND id IN (
                    SELECT id FROM core.taxon WHERE content_hash IS NULL LIMIT %s
                )
                """,
                (limit,),
            )
            n = cur.rowcount
            conn.commit()
    return n


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10000)
    args = parser.parse_args()
    n = backfill_taxa(limit=args.limit)
    print(f"Set content_hash on {n} taxa")


if __name__ == "__main__":
    main()
