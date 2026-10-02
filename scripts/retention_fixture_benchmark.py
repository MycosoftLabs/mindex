"""Small reproducible local PG benchmark; drops only guarded disposable schema.

Run from repo root: python -m scripts.retention_fixture_benchmark
This measures fixture database admission/readback, never S3 or production latency.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import math
import os
import platform
import time
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import sqlalchemy
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mindex_api.retention.contracts import Principal, RetentionConfig, admission_metadata
from mindex_api.retention.repository import RetentionRepository


def percentiles(values):
    ordered = sorted(values)
    return {'n': len(ordered), 'p50_ms': round(ordered[math.ceil(len(ordered) * .50) - 1], 3),
            'p95_ms': round(ordered[math.ceil(len(ordered) * .95) - 1], 3)}


def peak_rss_bytes():
    if os.name != 'nt':
        return None

    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'working', 'pool_peak', 'pool',
                                                'nonpaged_peak', 'nonpaged', 'pagefile', 'pagefile_peak')]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = ctypes.c_void_p
    read_counters = ctypes.windll.psapi.GetProcessMemoryInfo
    read_counters.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    return counters.peak if read_counters(get_process(), ctypes.byref(counters), counters.cb) else None


async def main():
    parsed = urlsplit(os.environ.get('RETENTION_TEST_DSN', '').replace('postgresql+asyncpg://', 'postgresql://', 1))
    if (os.environ.get('RETENTION_TEST_ALLOW_DISPOSABLE') != '1' or parsed.scheme != 'postgresql'
            or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or not parsed.path.startswith('/retention_fixture_') or parsed.query or parsed.fragment):
        raise RuntimeError('Refusing benchmark outside an explicitly allowed loopback fixture database')
    dsn = parsed.geturl()
    connection = await asyncpg.connect(dsn)
    version = await connection.fetchval('SELECT version()')
    await connection.execute('DROP SCHEMA IF EXISTS retention CASCADE')
    migration_dir = Path(__file__).parents[1] / 'migrations'
    for migration in (migration_dir / '20261001_shared_retention_v1.sql',
                      migration_dir / '20261002_private_orphan_reconciliation.sql'):
        await connection.execute(migration.read_text(encoding='utf-8'))
    await connection.close()
    engine = create_async_engine(dsn.replace('postgresql://', 'postgresql+asyncpg://', 1))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    actor = Principal('https://fixture.test', 'benchmark-user', 'fixture-tenant', 'fixture-project')
    async with sessions() as session, session.begin():
        await session.execute(text("""INSERT INTO retention.membership(issuer,subject,tenant_id,project_id,active)
            VALUES (:i,:s,:t,:p,true)"""), {'i': actor.issuer, 's': actor.subject, 't': actor.tenant_id, 'p': actor.project_id})
    await engine.dispose()  # cold admission includes a fresh connection/pool
    config = RetentionConfig(enabled=True)
    repo = RetentionRepository(sessions, config)
    payload = b'fixture-only-' + b'x' * (4096 - 13)
    admissions, reads, ids = [], [], []
    for index in range(51):
        metadata = admission_metadata('dataset', f'bench-{index}', 'application/octet-stream', None, config)
        start = time.perf_counter()
        result, created = await repo.admit(actor, metadata, payload)
        admissions.append((time.perf_counter() - start) * 1000)
        assert created and result['available_at'] is None
        ids.append(result['artifact_id'])
    for artifact_id in ids:
        start = time.perf_counter()
        row = await repo.get(actor, artifact_id)
        reads.append((time.perf_counter() - start) * 1000)
        assert bytes(row['payload']) == payload
    await engine.dispose()
    start = time.perf_counter()
    row = await RetentionRepository(sessions, config).get(actor, ids[0])
    restarted_read_ms = (time.perf_counter() - start) * 1000
    assert bytes(row['payload']) == payload
    await engine.dispose()
    print(json.dumps({'qualification': 'local PostgreSQL fixture only; no S3/issuer/production claims',
                      'python': platform.python_version(), 'os': platform.platform(), 'postgres': version,
                      'sqlalchemy': sqlalchemy.__version__, 'asyncpg': asyncpg.__version__,
                      'payload_bytes': len(payload), 'cold_admission_ms': round(admissions[0], 3),
                      'warm_admission': percentiles(admissions[1:]), 'warm_read': percentiles(reads[1:]),
                      'serial_admission_per_second': round(50000 / sum(admissions[1:]), 2),
                      'new_pool_read_ms': round(restarted_read_ms, 3), 'python_peak_rss_bytes': peak_rss_bytes(),
                      'correctness': '51 committed admissions and exact byte readbacks'}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
