#!/usr/bin/env python3
"""
Backfill core.taxon.kingdom, lineage, lineage_ids by walking parent_id chains.
Run after migration 20260502_all_life_universal.sql.

Usage:
  python -m mindex_etl.jobs.backfill_kingdom_lineage [--batch 5000] [--dsn $MINDEX_DATABASE_URL]
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

try:
    import asyncpg
except ImportError as e:  # pragma: no cover
    print("asyncpg required: pip install asyncpg", file=sys.stderr)
    raise SystemExit(1) from e

# Kingdom hints from rank + metadata (heuristic; refine with NCBI/GBIF ETL)
KINGDOM_ALIASES: dict[str, str] = {
    "fungi": "Fungi",
    "plantae": "Plantae",
    "animalia": "Animalia",
    "bacteria": "Bacteria",
    "archaea": "Archaea",
    "protista": "Protista",
    "protozoa": "Protista",
    "chromista": "Protista",
    "viruses": "Viruses",
    "viridae": "Viruses",
}


def _infer_kingdom_from_lineage(lineage: list[str], metadata: dict[str, Any] | None) -> str | None:
    if not lineage:
        return None
    low = [x.lower() for x in lineage if x]
    for part in low:
        for key, val in KINGDOM_ALIASES.items():
            if key in part.replace("_", " "):
                return val
    if metadata:
        for k in ("kingdom", "iconic_taxon_name", "kingdomKey"):
            v = metadata.get(k)
            if isinstance(v, str) and v.lower() in KINGDOM_ALIASES:
                return KINGDOM_ALIASES[v.lower()]
    return None


async def run_backfill(dsn: str, batch: int) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        # Load all taxa id, parent_id, canonical_name, metadata
        rows = await conn.fetch(
            """
            SELECT id, parent_id, canonical_name, COALESCE(metadata, '{}'::jsonb) AS metadata
            FROM core.taxon
            """
        )
        by_id: dict[str, dict[str, Any]] = {
            str(r["id"]): {
                "parent_id": str(r["parent_id"]) if r["parent_id"] else None,
                "canonical_name": r["canonical_name"] or "",
                "metadata": r["metadata"] if isinstance(r["metadata"], dict) else {},
            }
            for r in rows
        }

        def chain(tid: str) -> tuple[list[str], list[str]]:
            names: list[str] = []
            ids: list[str] = []
            seen: set[str] = set()
            cur: str | None = tid
            while cur and cur in by_id and cur not in seen:
                seen.add(cur)
                node = by_id[cur]
                n = (node.get("canonical_name") or "").strip()
                if n:
                    names.append(n)
                ids.append(cur)
                p = node.get("parent_id")
                cur = p if p in by_id else None
            names.reverse()
            ids.reverse()
            return names, ids

        updated = 0
        for r in rows:
            tid = str(r["id"])
            lineage, lineage_ids = chain(tid)
            meta: dict = by_id[tid].get("metadata") or {}
            k = _infer_kingdom_from_lineage(lineage, meta)
            if not k and lineage:
                # first element sometimes is kingdom
                k = lineage[0] if lineage[0] in (
                    "Fungi", "Plantae", "Animalia", "Bacteria", "Archaea", "Protista", "Viruses", "Undesignated"
                ) else None
            if not k:
                k = "Undesignated"
            await conn.execute(
                """
                UPDATE core.taxon
                SET kingdom = $1::text,
                    lineage = $2::text[],
                    lineage_ids = $3::uuid[]
                WHERE id = $4::uuid
                """,
                k,
                lineage,
                [x for x in lineage_ids],
                tid,
            )
            updated += 1
            if updated % batch == 0:
                print(f"backfill: updated {updated} / {len(rows)}")

        print(f"backfill: complete, updated {updated} taxa")
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--batch", type=int, default=5000, help="Log every N updates")
    args = p.parse_args()
    dsn = os.environ.get("MINDEX_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("Set MINDEX_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        raise SystemExit(1)
    import asyncio

    asyncio.run(run_backfill(dsn, args.batch))


if __name__ == "__main__":
    main()
