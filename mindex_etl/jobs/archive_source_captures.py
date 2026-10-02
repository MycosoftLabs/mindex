"""Explicit bounded retry worker. Does not create schema or schedule itself."""
from __future__ import annotations

import argparse
import asyncio
import json


async def run(limit: int):
    from mindex_api.config import settings
    from mindex_api.db import async_session_scope
    from mindex_api.source_capture import CaptureConfig, CaptureRepository, archive_one
    from mindex_api.source_capture_s3 import create_object_store

    config = CaptureConfig.from_settings(settings)
    config.admission()
    store = create_object_store(config)
    repository = CaptureRepository(async_session_scope, config)
    attempted = 0
    for _ in range(limit):
        if not await archive_one(repository, store):
            break
        attempted += 1
    return {"attempted": attempted, "scope": "bounded_worker_pass",
            "note": "Attempts are not proof that every capture was archived."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("limit must be between 1 and 100")
    try:
        print(json.dumps(asyncio.run(run(args.limit))))
    except Exception:
        print(json.dumps({"status": "unavailable", "detail": "capture_worker_unavailable"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
