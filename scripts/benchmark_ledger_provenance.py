"""Bounded offline baseline. No chain RPC, wallets, broadcasts or production DB.

Run from repo: .venv/Scripts/python.exe scripts/benchmark_ledger_provenance.py
Output is JSON; retain it with machine/runtime context. SQLite numbers do not
measure PostgreSQL, live artifact fetch latency, chain cost or chain finality.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import json
import math
import os
import platform
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mindex_api.ledger import provenance as p
from mindex_api.ledger.provenance_proof import make_fixture_proof, verify_fixture
from mindex_api.ledger.provenance_store import metadata


def distribution(samples):
    ordered = sorted(samples)
    return {"samples": len(samples), "cold_ms": samples[0],
            "p50_ms": ordered[max(0, math.ceil(len(samples)*0.5)-1)],
            "p95_ms": ordered[max(0, math.ceil(len(samples)*0.95)-1)],
            "warm_mean_ms": sum(samples[1:])/max(1, len(samples)-1)}


def peak_rss_bytes():
    if sys.platform == "win32":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = ctypes.c_void_p
        measure = ctypes.windll.psapi.GetProcessMemoryInfo
        measure.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        if measure(get_process(), ctypes.byref(counters), counters.cb):
            return counters.PeakWorkingSetSize
        return None
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss if sys.platform == "darwin" else rss * 1024
    except ImportError:
        return None


async def run(record_count, proof_count):
    start = time.perf_counter()
    source_key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"public benchmark key").digest())
    principal = p.ProvenancePrincipal("https://offline.invalid", "benchmark", "fixture", "fixture",
                                      frozenset({"ledger_operator"}))
    now = datetime.now(timezone.utc)
    trusted = p.TrustedSourceKey("benchmark", source_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex(), **principal.scope(),
        valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1))
    artifact_id = "25f6e2d6-00a9-478f-857a-cf08e93398c4"
    artifact_bytes = b"Offline benchmark retained private fixture. " * 1024
    artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
    digest_map = {artifact_id: artifact_digest}
    proof_samples, register_queue_samples, approval_queue_samples = [], [], []
    with tempfile.TemporaryDirectory(prefix="mindex-ledger-benchmark-") as directory:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(directory)/'fixture.sqlite'}",
            execution_options={"schema_translate_map": {"ledger": None}})
        ddl_started = time.perf_counter()
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        ddl_ms = (time.perf_counter()-ddl_started)*1000
        factory = async_sessionmaker(engine, expire_on_commit=False)
        for number in range(record_count):
            evidence = p.EvidenceEnvelope(source_class="synthetic", artifact_ids=[artifact_id],
                artifact_digests=digest_map, description=f"Offline benchmark {number}",
                rights=p.RightsMetadata(license_id="fixture", consent_reference="fixture"))
            source = p.SourceSignature(key_id="benchmark", signature_hex="0"*128,
                signed_at=now, expires_at=now+timedelta(minutes=30))
            source.signature_hex = source_key.sign(p.source_signing_message(principal, evidence, source)).hex()
            canonical = p.canonical_record_bytes(principal, evidence, source)
            request = p.RegisterRequest(idempotency_key=f"benchmark-record-{number}", evidence=evidence,
                source=source, content_hash=hashlib.sha256(canonical).hexdigest())
            async with factory() as db:
                tick = time.perf_counter()
                row = await p.register(db, principal, request)
                queued = await p.list_queue(db, principal, limit=100)
                assert any(item["record_id"] == row["id"] for item in queued)
                register_queue_samples.append((time.perf_counter()-tick)*1000)
                # Actual byte hash calculation included in source validation input.
                await p.validate(db, principal, row["id"], p.ValidateRequest(idempotency_key="benchmark-validate"),
                    trusted_key=trusted, artifact_hashes={artifact_id: hashlib.sha256(artifact_bytes).hexdigest()})
                tick = time.perf_counter()
                row = await p.approve(db, principal, row["id"], p.ApprovalRequest(
                    idempotency_key="benchmark-approve", policy_version="fixture-policy-v1",
                    privacy_review="accepted", equality_leak_review="accepted"))
            # New session demonstrates durable queue readback after committed approval.
            async with factory() as db:
                queued = await p.list_queue(db, principal, limit=100)
                assert any(item["record_id"] == row["id"] and item["status"] == "manual_submission_disabled"
                           for item in queued)
            approval_queue_samples.append((time.perf_counter()-tick)*1000)
        db_size = (Path(directory)/"fixture.sqlite").stat().st_size
        await engine.dispose()
        proof = make_fixture_proof(row["content_hash"], depth=3)
        for _ in range(proof_count):
            tick = time.perf_counter()
            result = verify_fixture(proof, commitment=row["content_hash"], chain=proof.chain,
                                    transaction_id=proof.transaction_id)
            proof_samples.append((time.perf_counter()-tick)*1000)
            assert result["state"] == "finalized" and not result["onchain_confirmed"]
    return {"schema_version": "mindex-ledger-offline-benchmark-v1", "measured_at": now.isoformat(),
        "qualification": "sqlite_offline_fixture_only", "comparison_baseline": "first measurement; no speedup claim",
        "hardware": {"platform": platform.platform(), "machine": platform.machine(),
                     "cpu": platform.processor(), "logical_cpus": os.cpu_count()},
        "software": {"python": platform.python_version(), "sqlalchemy": sqlalchemy.__version__,
                     "sqlite": sqlite3.sqlite_version},
        "dataset": {"records": record_count, "artifact_bytes": len(artifact_bytes),
                    "canonical_envelope_bytes": len(canonical), "fixture_database_bytes": db_size,
                    "fixture_proof_bytes": len(proof.model_dump_json().encode()), "proof_iterations": proof_count},
        "cold_schema_create_ms": ddl_ms,
        "registration_commit_and_queue_readback": distribution(register_queue_samples),
        "approval_commit_and_new_session_queue_readback": distribution(approval_queue_samples),
        "offline_verification": {**distribution(proof_samples),
                                 "proofs_per_second": proof_count/(sum(proof_samples)/1000)},
        "peak_rss_bytes": peak_rss_bytes(), "elapsed_seconds": time.perf_counter()-start,
        "unmeasured": ["PostgreSQL migration/concurrency", "live artifact fetch", "chain consensus/finality",
                       "chain transaction costs", "production database durability"],
        "correctness": "asserted local queue readback, fixture finality label and onchain_confirmed=false"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=25)
    parser.add_argument("--proofs", type=int, default=1000)
    args = parser.parse_args()
    if not 2 <= args.records <= 100 or not 2 <= args.proofs <= 10000:
        parser.error("bounded fixture requires records 2..100 and proofs 2..10000")
    print(json.dumps(asyncio.run(run(args.records, args.proofs)), indent=2))
