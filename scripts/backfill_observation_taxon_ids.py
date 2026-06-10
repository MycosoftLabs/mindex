#!/usr/bin/env python3
"""Backfill obs.observation.taxon_id from core.taxon_external_id (iNat source ids)."""
from __future__ import annotations

import argparse

from mindex_etl.db import db_session


def backfill(*, batch_size: int = 5000, max_batches: int = 200) -> int:
    updated = 0
    with db_session() as conn:
        with conn.cursor() as cur:
            for _ in range(max_batches):
                cur.execute(
                    """
                    WITH candidates AS (
                        SELECT o.id AS obs_id, t.id AS taxon_id
                        FROM obs.observation o
                        JOIN core.taxon_external_id te
                          ON te.source = o.source
                         AND te.source_id = o.metadata->>'taxon_inat_id'
                        JOIN core.taxon t ON t.id = te.taxon_id
                        WHERE o.taxon_id IS NULL
                          AND o.metadata->>'taxon_inat_id' IS NOT NULL
                        LIMIT %s
                    )
                    UPDATE obs.observation o
                    SET taxon_id = c.taxon_id
                    FROM candidates c
                    WHERE o.id = c.obs_id
                    RETURNING o.id
                    """,
                    (batch_size,),
                )
                rows = cur.fetchall()
                n = len(rows)
                updated += n
                conn.commit()
                if n < batch_size:
                    break
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--max-batches", type=int, default=200)
    args = parser.parse_args()
    total = backfill(batch_size=args.batch_size, max_batches=args.max_batches)
    print(f"Updated taxon_id on {total} observations")


if __name__ == "__main__":
    main()
