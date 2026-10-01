"""Explicit bounded archive/expiry/purge pass; never migrates or schedules itself."""
from __future__ import annotations

import argparse
import asyncio
import json


async def run(limit: int):
    from mindex_api.db import async_session_scope
    from mindex_api.retention.contracts import RetentionConfig
    from mindex_api.retention.repository import RetentionRepository
    from mindex_api.retention.object_store import create_object_store
    from mindex_api.retention.service import archive_one, purge_one

    config = RetentionConfig.from_env()
    config.admission()
    repo = RetentionRepository(async_session_scope, config)
    store = create_object_store(config)
    expired = await repo.expire(limit)
    archive_attempts = purge_attempts = 0
    for _ in range(limit):
        if not await archive_one(repo, store):
            break
        archive_attempts += 1
    for _ in range(limit):
        if not await purge_one(repo, store):
            break
        purge_attempts += 1
    # Counts are attempts, not invented successful artifacts or erasures.
    return {'contract_version': 'retention.v1', 'expired_access': expired,
            'archive_attempts': archive_attempts, 'purge_attempts': purge_attempts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=10, choices=range(1, 101), metavar='1..100')
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args.limit))))
    except Exception:
        print(json.dumps({'error': 'retention_worker_unavailable'}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
