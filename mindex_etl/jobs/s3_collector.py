"""
S3-compatible object inventory collector.

Lists the MINDEX cold bucket (``AWS_S3_MINDEX_BUCKET``) and records its size and
object count into ``network.storage_node`` so the federation view knows how much
data lives in AWS. Degrades to a no-op (exit 0, no DB writes) when boto3 or
credentials are absent — never emits mock rows.

Run:
  python -m mindex_etl.jobs.s3_collector            # inventory once
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def collect_s3_inventory() -> int:
    """Inventory the S3 bucket into network.storage_node. Returns object count."""
    bucket = os.environ.get("AWS_S3_MINDEX_BUCKET") or os.environ.get("S3_BUCKET")
    if not bucket:
        logger.info("AWS_S3_MINDEX_BUCKET/S3_BUCKET not set — skipping S3 inventory.")
        return 0

    try:
        import boto3  # type: ignore
    except Exception as exc:  # pragma: no cover
        logger.info("boto3 unavailable (%s) — skipping S3 inventory.", exc)
        return 0

    kwargs = {}
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if region:
        kwargs["region_name"] = region
    endpoint = os.getenv("S3_ENDPOINT_URL")
    if endpoint:
        kwargs["endpoint_url"] = endpoint

    try:
        client = boto3.client("s3", **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not create S3 client: %s", exc)
        return 0

    total_objects = 0
    total_bytes = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []) or []:
                total_objects += 1
                total_bytes += int(obj.get("Size", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("S3 list for %s failed: %s", bucket, exc)
        return 0

    logger.info("S3 inventory: %s objects, %.1f GB in %s",
                f"{total_objects:,}", total_bytes / (1024 ** 3), bucket)
    _upsert_storage_node(bucket, total_objects, total_bytes)
    return total_objects


def _upsert_storage_node(bucket: str, objects: int, used_bytes: int) -> None:
    """Record the bucket as an S3 storage node for federation."""
    try:
        from psycopg.types.json import Json  # type: ignore

        from ..db import db_session
    except Exception as exc:  # pragma: no cover
        logger.debug("Storage node upsert skipped (no psycopg): %s", exc)
        return

    label = f"S3 {bucket}"
    try:
        with db_session() as conn:
            cur = conn.execute("SELECT id FROM network.storage_node WHERE label = %s", (label,))
            row = cur.fetchone()
            meta = Json({"bucket": bucket, "provider": "aws_s3", "role": "cold_backup"})
            if row:
                conn.execute(
                    """
                    UPDATE network.storage_node
                    SET used_bytes = %s, last_seen_at = NOW(), metadata = %s
                    WHERE label = %s
                    """,
                    (used_bytes, meta, label),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO network.storage_node
                        (kind, label, host, region, used_bytes, owner, last_seen_at, metadata)
                    VALUES ('s3', %s, %s, %s, %s, 'mycosoft', NOW(), %s)
                    """,
                    (label, bucket, os.getenv("AWS_REGION", "us-east-1"), used_bytes, meta),
                )
        logger.info("network.storage_node updated for %s", label)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Storage node upsert failed: %s", exc)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    argparse.ArgumentParser(description="S3 inventory collector").parse_args()
    collect_s3_inventory()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
