"""
NAS / UniFi-attached storage inventory collector (stub).

Scans `NAS_HOST` / `NAS_EXPORT_PATH` when implemented. Does **not** write placeholder rows.

Configure:
  NAS_HOST=192.168.0.105
  NAS_EXPORT_PATH=/mycosoft.com/...

Run:
  python -m mindex_etl.jobs.nas_collector --help
"""

from __future__ import annotations

import argparse
import logging
import os

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="NAS collector (implementation pending — no mock inserts).")
    p.parse_args()
    host = os.environ.get("NAS_HOST", "").strip()
    if not host:
        logger.info("NAS_HOST not set — nothing to scan. Exiting 0.")
        return 0
    logger.info("NAS collector stub: would scan %s (no DB writes in stub).", host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
