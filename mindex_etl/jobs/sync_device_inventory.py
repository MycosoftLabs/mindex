"""
Sync `telemetry.device` rows into `devices.inventory` (MINDEX federation table).

Real data only — reads existing telemetry devices in Postgres; no fabricated rows.

  set PYTHONPATH=.
  set MINDEX_DB_DSN=postgresql://...
  python -m mindex_etl.jobs.sync_device_inventory

Optional: `--dry-run` to log counts only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Sequence

import psycopg

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


UPSERT_SQL = """
INSERT INTO devices.inventory (device_key, device_type, serial, status, metadata)
VALUES (%(device_key)s, 'telemetry', NULL, %(status)s, CAST(%(metadata)s AS jsonb))
ON CONFLICT (device_key) DO UPDATE SET
    status = EXCLUDED.status,
    metadata = devices.inventory.metadata || EXCLUDED.metadata,
    updated_at = now()
"""


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(list(argv) if argv is not None else None)

    dsn = _load_dsn()
    if not dsn:
        logger.error("MINDEX_DB_DSN missing")
        return 2

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, slug, name, status, metadata
                FROM telemetry.device
                ORDER BY updated_at DESC NULLS LAST, created_at DESC
                """
            )
            rows = cur.fetchall()
        n = 0
        for rid, slug, name, status, metadata in rows:
            device_key = (slug or "").strip() or f"telemetry:{rid}"
            meta: dict[str, Any] = {"telemetry_device_id": rid, "display_name": name}
            if isinstance(metadata, dict):
                meta.update(metadata)
            payload = {
                "device_key": device_key[:256],
                "status": (status or "unknown")[:32],
                "metadata": json.dumps(meta),
            }
            if args.dry_run:
                n += 1
                continue
            with conn.cursor() as cur2:
                cur2.execute(UPSERT_SQL, payload)
            n += 1
        if not args.dry_run:
            conn.commit()

    logger.info("%s %s devices", "would upsert" if args.dry_run else "upserted", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
