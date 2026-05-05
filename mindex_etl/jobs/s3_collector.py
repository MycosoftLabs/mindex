"""
S3-compatible object inventory collector (stub).

Requires `S3_ENDPOINT`, `S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (or compatible).
No mock object listings — when credentials missing, exits 0 without DB writes.

Run:
  python -m mindex_etl.jobs.s3_collector --help
"""

from __future__ import annotations

import argparse
import logging
import os

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="S3 collector (boto3 wiring pending).")
    p.parse_args()
    if not os.environ.get("S3_BUCKET"):
        logger.info("S3_BUCKET not set — skipping.")
        return 0
    logger.info("S3 collector stub: configure boto3 ListObjectsV2 and network.storage_node upserts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
