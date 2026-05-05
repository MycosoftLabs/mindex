"""
Device storage / shard telemetry collector (stub).

Intended to join MycoBrain + MAS device registry snapshots into `network.storage_node`.
No synthetic capacity numbers — implement HTTP/MAS pulls behind env flags.

Run:
  python -m mindex_etl.jobs.device_storage_collector
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    if not os.environ.get("MAS_BASE_URL"):
        logger.info("MAS_BASE_URL not set — nothing to collect.")
        return 0
    logger.info("device_storage_collector stub: wire httpx -> MAS /api/devices + MINDEX /network/nodes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
