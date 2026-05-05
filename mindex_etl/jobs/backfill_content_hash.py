"""
Backfill `content_hash` + `content_hashed_at` on core.taxon, bio.genome, bio.taxon_compound.

Uses a deterministic SHA-256 over a stable JSON projection (sorted keys, ISO datetimes as strings).
Requires migration `0031_mindex_app_overhaul.sql` applied (columns must exist).

Run on VM 189 (or any host with DB reachability):

  set PYTHONPATH=.
  set MINDEX_DB_DSN=postgresql://...
  python -m mindex_etl.jobs.backfill_content_hash --batch 500

  # Dry run (no writes)
  python -m mindex_etl.jobs.backfill_content_hash --dry-run --batch 50
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


def _load_dsn() -> str:
    dsn = (os.environ.get("MINDEX_DB_DSN") or "").strip()
    if dsn:
        if dsn.startswith("postgresql+asyncpg://"):
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        return dsn
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from mindex_api.config import settings  # type: ignore

    u = str(settings.mindex_db_dsn)
    if u.startswith("postgresql+asyncpg://"):
        u = u.replace("postgresql+asyncpg://", "postgresql://", 1)
    return u


def _json_default(obj: Any) -> str:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    raise TypeError(f"unsupported type {type(obj)}")


def _canonical_json_bytes(row: Mapping[str, Any], *, exclude: set[str]) -> bytes:
    data = {k: v for k, v in row.items() if k not in exclude}
    return json.dumps(data, sort_keys=True, default=_json_default).encode("utf-8")


def _hash_row(row: Mapping[str, Any], *, exclude: set[str]) -> bytes:
    return hashlib.sha256(_canonical_json_bytes(row, exclude=exclude)).digest()


def backfill_taxon(conn: psycopg.Connection, *, batch: int, dry_run: bool) -> int:
    exclude = {"content_hash", "content_hashed_at"}
    sql = """
        SELECT id, parent_id, canonical_name, rank, common_name, authority, description, source,
               metadata, created_at, updated_at
        FROM core.taxon
        WHERE content_hash IS NULL
        ORDER BY id
    """
    updated = 0
    with conn.cursor(row_factory=dict_row) as cur:
        cur.itersize = batch
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            for row in rows:
                h = _hash_row(row, exclude=exclude)
                if dry_run:
                    updated += 1
                    continue
                conn.execute(
                    """
                    UPDATE core.taxon
                    SET content_hash = %s, content_hashed_at = now()
                    WHERE id = %s AND content_hash IS NULL
                    """,
                    (h, row["id"]),
                )
                updated += int(conn.rowcount or 0)
        conn.commit()
    return updated


def backfill_genome(conn: psycopg.Connection, *, batch: int, dry_run: bool) -> int:
    exclude = {"content_hash", "content_hashed_at"}
    sql = """
        SELECT id, taxon_id, source, accession, assembly_level, release_date, metadata, created_at
        FROM bio.genome
        WHERE content_hash IS NULL
        ORDER BY id
    """
    updated = 0
    with conn.cursor(row_factory=dict_row) as cur:
        cur.itersize = batch
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            for row in rows:
                h = _hash_row(row, exclude=exclude)
                if dry_run:
                    updated += 1
                    continue
                conn.execute(
                    """
                    UPDATE bio.genome
                    SET content_hash = %s, content_hashed_at = now()
                    WHERE id = %s AND content_hash IS NULL
                    """,
                    (h, row["id"]),
                )
                updated += int(conn.rowcount or 0)
        conn.commit()
    return updated


def backfill_taxon_compound(conn: psycopg.Connection, *, batch: int, dry_run: bool) -> int:
    exclude = {"content_hash", "content_hashed_at"}
    sql = """
        SELECT id, taxon_id, compound_id, relationship_type, evidence_level, metadata, created_at
        FROM bio.taxon_compound
        WHERE content_hash IS NULL
        ORDER BY id
    """
    updated = 0
    with conn.cursor(row_factory=dict_row) as cur:
        cur.itersize = batch
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            for row in rows:
                h = _hash_row(row, exclude=exclude)
                if dry_run:
                    updated += 1
                    continue
                conn.execute(
                    """
                    UPDATE bio.taxon_compound
                    SET content_hash = %s, content_hashed_at = now()
                    WHERE id = %s AND content_hash IS NULL
                    """,
                    (h, row["id"]),
                )
                updated += int(conn.rowcount or 0)
        conn.commit()
    return updated


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--batch", type=int, default=500, help="Rows per fetch batch")
    p.add_argument("--dry-run", action="store_true", help="Count rows that would update, no writes")
    args = p.parse_args(list(argv) if argv is not None else None)

    dsn = _load_dsn()
    if not dsn:
        logger.error("MINDEX_DB_DSN missing and mindex_api.config unavailable")
        return 2

    with psycopg.connect(dsn, autocommit=False) as conn:
        t = backfill_taxon(conn, batch=args.batch, dry_run=args.dry_run)
        g = backfill_genome(conn, batch=args.batch, dry_run=args.dry_run)
        tc = backfill_taxon_compound(conn, batch=args.batch, dry_run=args.dry_run)

    mode = "dry-run rows" if args.dry_run else "rows updated"
    logger.info("core.taxon %s: %s", mode, t)
    logger.info("bio.genome %s: %s", mode, g)
    logger.info("bio.taxon_compound %s: %s", mode, tc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
