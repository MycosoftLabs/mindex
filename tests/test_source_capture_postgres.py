"""Opt-in disposable PostgreSQL integration, runnable without pytest.

Requires RETENTION_TEST_ISOLATED=1 and RETENTION_TEST_DSN pointing ONLY to the
synthetic retention_fixture database/user at retention-db on an isolated Docker
network. This script creates/truncates its fixture schema. Never use a live DSN.
No AWS calls: the real object adapter uses an explicitly synthetic object store.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]
PKG = "retention_postgres_fixture"
package = types.ModuleType(PKG)
package.__path__ = []
sys.modules[PKG] = package


def load(name):
    spec = importlib.util.spec_from_file_location(PKG + "." + name, ROOT / "mindex_api" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = load("source_capture")
s3 = load("source_capture_s3")


def fixture_dsn():
    dsn = os.environ.get("RETENTION_TEST_DSN", "")
    url = make_url(dsn)
    if (os.environ.get("RETENTION_TEST_ISOLATED") != "1" or url.host != "retention-db"
            or url.username != "retention_fixture" or url.database not in {"retention_fixture", "retention_restore"}
            or url.drivername != "postgresql+psycopg" or url.query):
        raise RuntimeError("Explicit disposable retention-db fixture required")
    return dsn


def config(**kwargs):
    return replace(core.CaptureConfig(enabled=True, sources=("fixture-public",),
        bucket="fixture-private", prefix="qualification/retention",
        expected_owner="000000000000", region="us-east-1",
        kms_key="arn:aws:kms:us-east-1:000000000000:key/fixture"), **kwargs)


def metadata(key, cfg=None, observed=None):
    return core.capture_metadata("fixture-public", key, "application/json", "identity", observed, cfg or config())


class ConditionalExists(Exception):
    response = {"Error": {"Code": "PreconditionFailed"}}


class FixtureS3:
    def __init__(self):
        self.objects = {}
        self.ambiguous = False
        self.corrupt = False

    def get_bucket_versioning(self, **_kwargs):
        return {"Status": "Enabled"}

    def put_object(self, **kwargs):
        if kwargs["Key"] in self.objects:
            raise ConditionalExists()
        self.objects[kwargs["Key"]] = kwargs
        if self.ambiguous:
            self.ambiguous = False
            raise TimeoutError("synthetic timeout after object persistence")
        return {"VersionId": "fixture-v1"}

    def head_object(self, **_kwargs):
        return {"VersionId": "fixture-v1"}

    def get_object(self, **kwargs):
        item = self.objects[kwargs["Key"]]
        return {"Body": io.BytesIO(b"bad" if self.corrupt else item["Body"]),
            "VersionId": "fixture-v1", "ContentLength": len(item["Body"]),
            "ServerSideEncryption": "aws:kms", "SSEKMSKeyId": item["SSEKMSKeyId"],
            "Metadata": item["Metadata"]}


async def crash_child(mode):
    engine = create_async_engine(fixture_dsn())
    class ExitBeforeCommit(AsyncSession):
        async def commit(self):
            os._exit(74)
    factory = async_sessionmaker(engine, class_=ExitBeforeCommit if mode == "uncommitted" else AsyncSession)
    repository = core.CaptureRepository(factory, config())
    await repository.accept("fixture-service", metadata("crash-" + mode), b"fixture-crash")
    os._exit(73)


async def run_checks():
    dsn = fixture_dsn()
    assert make_url(dsn).database == "retention_fixture", "Suite may reset only its original fixture DB"
    import psycopg
    # Explicit migration only against the guarded disposable database.
    with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as connection:
        sql = (ROOT / "migrations/20260930_source_capture_retention.sql").read_text()
        connection.execute(sql)
        connection.execute(sql)  # idempotent setup
    engine = create_async_engine(dsn, pool_size=5, max_overflow=10)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = core.CaptureRepository(sessions, config())
    checks = ["migration_applied_twice"]

    async def statement(sql, parameters=None):
        async with engine.begin() as db:
            return await db.execute(text(sql), parameters or {})

    async def clean():
        await statement("TRUNCATE raw_source.capture")

    await clean()
    raw = b' { "fixture": true }\n'
    first, created = await repository.accept("fixture-service", metadata("first"), raw)
    row = await repository.get(first["capture_id"])
    assert created and first["state"] == "pending_archive" and first["cloud_verified"] is False
    assert bytes(row["payload"]) == raw and row["observed_at"] is None
    assert row["sha256"] == hashlib.sha256(raw).hexdigest()
    checks.append("committed_exact_bytes_pending_receipt")
    duplicate, created = await repository.accept("fixture-service", metadata("first"), raw)
    assert not created and duplicate["capture_id"] == first["capture_id"]
    checks.append("identical_retry_same_capture")
    for body, meta in [(b"changed", metadata("first")),
                       (raw, metadata("first", observed="2026-01-01T00:00:00Z"))]:
        try:
            await repository.accept("fixture-service", meta, body)
            raise AssertionError("conflicting payload or metadata accepted")
        except core.CaptureError as exc:
            assert exc.code == "idempotency_conflict" and exc.status == 409
    checks.append("payload_and_metadata_conflicts_409")
    distinct, _ = await repository.accept("fixture-service", metadata("next-observation"), raw)
    assert distinct["capture_id"] != first["capture_id"]
    checks.append("distinct_observations_not_content_deduplicated")

    await clean()
    same = await asyncio.gather(*(repository.accept("fixture-service", metadata("concurrent"), raw) for _ in range(8)))
    assert len({result[0]["capture_id"] for result in same}) == 1 and sum(result[1] for result in same) == 1
    checks.append("concurrent_idempotency_one_row")
    claims = await asyncio.gather(repository.claim(), repository.claim())
    assert sum(row is not None for row in claims) == 1
    old = next(row for row in claims if row)
    checks.append("concurrent_claim_single_lease")
    await statement("UPDATE raw_source.capture SET lease_expires_at=clock_timestamp()-interval '1 second'")
    new = await repository.claim()
    assert new["lease_token"] != old["lease_token"]
    obj = {"bucket": "fixture", "key": "fixture", "version": "fixture-v1"}
    assert not await repository.complete(old, obj)
    assert not await repository.retry(old, "archive_unavailable")
    assert await repository.retry(new, "archive_unavailable")
    checks.append("expired_lease_recovery_and_stale_worker_fencing")

    await clean()
    tiny = core.CaptureRepository(sessions, config(max_payload_bytes=4, max_pending_bytes=8, max_pending_count=2))
    async def small(i):
        try:
            return await tiny.accept("fixture-service", metadata(f"small-{i}", tiny.config), b"1234")
        except core.CaptureError as exc:
            assert exc.code == "capture_capacity_exhausted"
            return None
    results = await asyncio.gather(*(small(i) for i in range(8)))
    assert sum(item is not None for item in results) == 2
    sizes = (await statement("""SELECT sum(byte_length) AS pending_payload_bytes,
        pg_total_relation_size('raw_source.capture') AS relation_bytes_including_indexes
        FROM raw_source.capture WHERE payload IS NOT NULL""")).mappings().one()
    assert sizes["pending_payload_bytes"] == 8 and sizes["relation_bytes_including_indexes"] > 8
    checks.append("concurrent_capacity_payload_vs_full_db_bytes")

    await clean()
    for mode, exit_code in [("committed", 73), ("uncommitted", 74)]:
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-B", str(Path(__file__).resolve()), "--crash", mode],
            check=False, capture_output=True, timeout=30)
        assert result.returncode == exit_code, "fixture crash subprocess failed"
    stored = (await statement("SELECT idempotency_key FROM raw_source.capture")).scalars().all()
    assert stored == ["crash-committed"]
    checks.append("actual_process_crash_commit_survives_uncommitted_rolls_back")

    await clean()
    accepted, _ = await repository.accept("fixture-service", metadata("cloud-retry"), raw)
    fake = FixtureS3()
    fake.ambiguous = True
    store = s3.CaptureObjectStore(fake, config())
    assert await core.archive_one(repository, store)
    pending = await repository.get(accepted["capture_id"])
    assert pending["state"] == "pending_archive" and bytes(pending["payload"]) == raw
    assert pending["attempt_count"] == 1 and pending["last_error_code"] == "archive_unavailable"
    checks.append("ambiguous_cloud_write_retains_pending_payload_and_retry")
    await statement("UPDATE raw_source.capture SET next_attempt_at=clock_timestamp()")
    restarted = core.CaptureRepository(sessions, config())
    assert await core.archive_one(restarted, store)
    archived = await restarted.get(accepted["capture_id"])
    assert archived["state"] == "archived_verified" and archived["payload"] is None
    assert archived["object_version"] == "fixture-v1" and len(fake.objects) == 1
    assert store.read(archived) == raw
    checks.append("retry_reconciles_existing_version_before_releasing_pending_bytes")
    fake.corrupt = True
    try:
        store.read(archived)
        raise AssertionError("corrupt archive returned")
    except core.CaptureError as exc:
        assert exc.code == "capture_integrity_failed"
    checks.append("archived_retrieval_recomputes_checksum")

    await clean()
    accepted, _ = await repository.accept("fixture-service", metadata("integrity"), raw)
    fake = FixtureS3()
    fake.corrupt = True
    assert await core.archive_one(repository, s3.CaptureObjectStore(fake, config()))
    blocked = await repository.get(accepted["capture_id"])
    assert blocked["state"] == "integrity_blocked" and bytes(blocked["payload"]) == raw
    assert await repository.claim() is None
    checks.append("failed_post_upload_verifier_blocks_without_dropping_bytes")

    await clean()
    accepted, _ = await repository.accept("fixture-service", metadata("finalize-failure"), raw)
    claimed = await repository.claim()
    fake = FixtureS3()
    store = s3.CaptureObjectStore(fake, config())
    obj = store.archive(claimed)
    class FailingCommit(AsyncSession):
        async def commit(self):
            raise OSError("synthetic commit failure")
    failed = core.CaptureRepository(async_sessionmaker(engine, class_=FailingCommit), config())
    try:
        await failed.complete(claimed, obj)
        raise AssertionError("failed final commit reported success")
    except core.CaptureError as exc:
        assert exc.code == "capture_storage_unavailable"
    preserved = await repository.get(accepted["capture_id"])
    assert preserved["state"] == "archiving" and bytes(preserved["payload"]) == raw
    await statement("UPDATE raw_source.capture SET lease_expires_at=clock_timestamp()-interval '1 second'")
    assert await core.archive_one(repository, store)
    assert (await repository.get(accepted["capture_id"]))["state"] == "archived_verified"
    checks.append("final_commit_failure_recovery_without_duplicate_object")
    try:
        await failed.accept("fixture-service", metadata("admission-failure"), raw)
        raise AssertionError("failed admission commit acknowledged")
    except core.CaptureError:
        assert (await statement("SELECT count(*) FROM raw_source.capture WHERE idempotency_key='admission-failure'")).scalar() == 0
    checks.append("failed_admission_commit_never_acknowledges")

    # Restore a metadata/index snapshot into a second disposable schema/table,
    # retrieve its indexed object version, and compare exact raw bytes. This is
    # a logical fixture restore, not a production pg_dump disaster-recovery test.
    snapshot = await repository.get(accepted["capture_id"])
    # Use a fixture-only sibling table: pooled sessions may differ.
    await statement("DROP TABLE IF EXISTS raw_source.restore_fixture")
    await statement("CREATE TABLE raw_source.restore_fixture AS SELECT * FROM raw_source.capture")
    restored = dict((await statement("SELECT * FROM raw_source.restore_fixture WHERE capture_id=:id",
                                    {"id": snapshot["capture_id"]})).mappings().one())
    assert store.read(restored) == raw
    checks.append("logical_index_restore_retrieves_same_version_and_checksum")
    await statement("DROP TABLE raw_source.restore_fixture")
    await statement("ALTER TABLE raw_source.capture RENAME TO capture_hidden")
    try:
        try:
            await repository.accept("fixture-service", metadata("missing-schema"), raw)
            raise AssertionError("missing schema accepted")
        except core.CaptureError as exc:
            assert exc.code == "capture_storage_unavailable"
    finally:
        await statement("ALTER TABLE raw_source.capture_hidden RENAME TO capture")
    checks.append("real_missing_table_failure_sanitized")
    # Explicitly synthetic object export for a subsequent real pg_dump/restore
    # qualification. The local test operator owns this disposable directory.
    await repository.accept("fixture-service", metadata("restore-pending"), raw)
    export = {key: {**value, "Body": base64.b64encode(value["Body"]).decode()}
              for key, value in fake.objects.items()}
    (Path(__file__).parent / "source_capture_fixture_objects.json").write_text(json.dumps(export))
    await engine.dispose()
    return {"passed": len(checks), "checks": checks, "physical_size_example": dict(sizes),
        "limits": ["Real isolated PostgreSQL, synthetic payloads and object-store fixture",
                   "Not a live AWS/IAM/service deployment or production backup restore test"]}


async def verify_restored():
    dsn = fixture_dsn()
    assert make_url(dsn).database == "retention_restore", "Restore check requires a separate fixture DB"
    engine = create_async_engine(dsn)
    async with engine.connect() as db:
        rows = (await db.execute(text("SELECT * FROM raw_source.capture"))).mappings().all()
    fake = FixtureS3()
    export = json.loads((Path(__file__).parent / "source_capture_fixture_objects.json").read_text())
    fake.objects = {key: {**value, "Body": base64.b64decode(value["Body"])} for key, value in export.items()}
    store = s3.CaptureObjectStore(fake, config())
    archived = pending = 0
    for row in rows:
        if row["state"] == "archived_verified":
            payload = store.read(row)
            archived += 1
        else:
            payload = bytes(row["payload"])
            pending += 1
        assert hashlib.sha256(payload).hexdigest() == row["sha256"]
        assert len(payload) == row["byte_length"]
    assert archived == 1 and pending == 1
    await engine.dispose()
    return {"status": "passed", "archived_versions_retrieved": archived,
            "pending_payloads_recovered": pending,
            "scope": "Separate real pg_dump/pg_restore fixture database with synthetic object store"}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--crash":
        asyncio.run(crash_child(sys.argv[2]))
    elif sys.argv[1:] == ["--verify-restored"]:
        print(json.dumps(asyncio.run(verify_restored()), indent=2))
    else:
        print(json.dumps(asyncio.run(run_checks()), indent=2, default=str))


def test_opt_in_postgres_contract():
    import pytest
    if os.environ.get("RETENTION_TEST_ISOLATED") != "1":
        pytest.skip("Explicit isolated PostgreSQL fixture not configured")
    assert asyncio.run(run_checks())["passed"] >= 18
