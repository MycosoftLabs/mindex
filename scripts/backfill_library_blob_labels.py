#!/usr/bin/env python3
"""
Backfill library.blob catalog columns for existing acoustic rows (ESC-50, MBARI).

Run on MINDEX VM after migration 20260604_library_blob_labels_may27_2026.sql:
  python scripts/backfill_library_blob_labels.py --source esc50
  python scripts/backfill_library_blob_labels.py --source mbari_pacific_sound
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mindex_etl.db import db_session
from mindex_etl.library.catalog_record import catalog_from_esc50, catalog_from_mbari
from mindex_etl.library.nlm_source_registry import upsert_sources
from mindex_etl.library.sources_esc50 import _load_esc50_meta
import io
import zipfile

import httpx

from mindex_etl.library.sources_esc50 import ESC50_ZIP_URL

logger = logging.getLogger(__name__)


def backfill_esc50(conn, limit: int) -> int:
    logger.info("Loading ESC-50 metadata for backfill...")
    with httpx.Client(timeout=600, follow_redirects=True) as client:
        resp = client.get(ESC50_ZIP_URL)
        resp.raise_for_status()
        data = resp.content
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        meta_by_file = _load_esc50_meta(zf)

    updated = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, filename, metadata
            FROM library.blob
            WHERE source_id = 'esc50'
              AND (label_primary IS NULL OR title IS NULL)
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    for blob_id, filename, _meta in rows:
        row = meta_by_file.get(filename, {})
        if not row:
            continue
        catalog = catalog_from_esc50(filename, row)
        kw = catalog.to_db_kwargs()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE library.blob SET
                    title = %s, description = %s, label_primary = %s, label_secondary = %s,
                    acoustic_environment = %s, source_name = %s, source_url = %s,
                    origin_dataset_id = %s, nlm_subsystem = %s, nlm_priority = %s,
                    fold_id = %s, training_split = %s, locale = %s,
                    metadata = metadata || %s::jsonb
                WHERE id = %s
                """,
                (
                    kw["title"],
                    kw["description"],
                    kw["label_primary"],
                    kw["label_secondary"],
                    kw["acoustic_environment"],
                    kw["source_name"],
                    kw["source_url"],
                    kw["origin_dataset_id"],
                    kw["nlm_subsystem"],
                    kw["nlm_priority"],
                    kw["fold_id"],
                    kw["training_split"],
                    kw["locale"],
                    __import__("json").dumps(kw["metadata"]),
                    blob_id,
                ),
            )
        updated += 1
    return updated


def backfill_mbari(conn, limit: int) -> int:
    updated = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, filename, metadata
            FROM library.blob
            WHERE source_id = 'mbari_pacific_sound'
              AND (label_primary IS NULL OR title IS NULL)
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    for blob_id, filename, meta in rows:
        meta = meta or {}
        bucket = meta.get("s3_bucket") or "pacific-sound-2khz"
        key = meta.get("s3_key") or filename
        if isinstance(meta, str):
            import json
            try:
                meta = json.loads(meta)
                bucket = meta.get("s3_bucket", bucket)
                key = meta.get("s3_key", key)
            except Exception:
                pass
        # legacy: metadata may store s3_key as "bucket/key"
        if "/" in str(key) and not meta.get("s3_bucket"):
            parts = str(key).split("/", 1)
            if len(parts) == 2:
                bucket, key = parts[0], parts[1]
        catalog = catalog_from_mbari(str(key), str(bucket))
        kw = catalog.to_db_kwargs()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE library.blob SET
                    title = %s, description = %s, label_primary = %s,
                    acoustic_environment = %s, source_name = %s, source_url = %s,
                    origin_dataset_id = %s, nlm_subsystem = %s, nlm_priority = %s,
                    capture_time_utc = %s, locale = %s,
                    metadata = metadata || %s::jsonb
                WHERE id = %s
                """,
                (
                    kw["title"],
                    kw["description"],
                    kw["label_primary"],
                    kw["acoustic_environment"],
                    kw["source_name"],
                    kw["source_url"],
                    kw["origin_dataset_id"],
                    kw["nlm_subsystem"],
                    kw["nlm_priority"],
                    kw["capture_time_utc"],
                    kw["locale"],
                    __import__("json").dumps(kw["metadata"]),
                    blob_id,
                ),
            )
        updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=["esc50", "mbari_pacific_sound", "all"])
    parser.add_argument("--limit", type=int, default=100000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    with db_session() as conn:
        upsert_sources(conn)
        total = 0
        if args.source in ("esc50", "all"):
            total += backfill_esc50(conn, args.limit)
        if args.source in ("mbari_pacific_sound", "all"):
            total += backfill_mbari(conn, args.limit)
        conn.commit()
    logger.info("Backfill updated %s rows", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
