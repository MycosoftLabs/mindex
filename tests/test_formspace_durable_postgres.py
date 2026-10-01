"""Opt-in REAL PostgreSQL transaction fixture, never a production connection.

Requires shared retention.v1 integrated, an EMPTY disposable local database named
formspace_fixture_<suffix>, and FORMSPACE_TEST_DATABASE_URL. No fallback to app DSN.
Fixture leaves created schemas in that disposable DB for inspection; reruns use a
new empty database. It never connects to an inherited DATABASE_URL or deletes data.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mindex_api.formspace.contracts import FormSpaceError
from mindex_api.formspace.repository import FormSpaceRepository
from test_formspace_durable import experiment


@pytest.mark.asyncio
async def test_real_postgres_admission_restart_fence_cancel_and_revocation():
    dsn = os.environ.get("FORMSPACE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("FORMSPACE_TEST_DATABASE_URL absent; no disposable PostgreSQL available")
    parsed = urlsplit(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    if (parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or not parsed.path.removeprefix("/").startswith("formspace_fixture_")
            or parsed.query or parsed.fragment):
        pytest.fail("Fixture refuses a nonlocal/non-formspace_fixture_ database")
    from mindex_api.retention.contracts import Principal, RetentionConfig, RetentionError
    from mindex_api.retention.repository import RetentionRepository
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def sessions():
        async with factory() as session:
            yield session
    try:
        async with engine.begin() as conn:
            existing = (await conn.execute(text("SELECT schema_name FROM information_schema.schemata WHERE schema_name IN ('retention','formspace')"))).all()
            if existing:
                pytest.fail("Fixture requires empty disposable DB; schemas already exist; nothing changed")
            # Minimal shared-membership FIXTURE table uses actual shared repository
            # authorization. Full retention archive qualification is a separate test.
            await conn.execute(text("CREATE SCHEMA retention"))
            await conn.execute(text("""CREATE TABLE retention.membership(
                issuer text,subject text,tenant_id uuid,project_id uuid,active boolean,
                PRIMARY KEY(issuer,subject,tenant_id,project_id))"""))
        migration = (Path(__file__).parents[1] / "migrations/20261001_formspace_durable.sql").read_text()
        # asyncpg supports migration scripts through its native driver, one server
        # command, preserving the migration's explicit transaction exactly.
        async with engine.connect() as conn:
            raw = await conn.get_raw_connection()
            await raw.driver_connection.execute(migration)
        principal = Principal("https://fixture.invalid", "alice",
            "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
        bob = Principal(principal.issuer, "bob", principal.tenant_id, principal.project_id)
        other_project = Principal(principal.issuer, principal.subject, principal.tenant_id,
                                  "33333333-3333-4333-8333-333333333333")
        async with sessions() as session, session.begin():
            for owner in (principal, bob, other_project):
                await session.execute(text("""INSERT INTO retention.membership
                    VALUES (:issuer,:subject,:tenant_id,:project_id,true)"""), owner.__dict__)
        shared = RetentionRepository(sessions, RetentionConfig(enabled=True))
        repository = FormSpaceRepository(shared, Principal)
        attempts = await asyncio.gather(*[
            repository.admit(principal, "same", experiment()) for _ in range(4)])
        assert len({row["job_id"] for row, _ in attempts}) == 1
        assert sum(created for _, created in attempts) == 1
        job_id = attempts[0][0]["job_id"]
        async with sessions() as session:
            assert (await session.execute(text("SELECT count(*) FROM formspace.outbox"))).scalar_one() == 1
        changed = experiment()
        changed["parameters"]["dt"] = 0.2
        with pytest.raises(FormSpaceError, match="idempotency_conflict"):
            await repository.admit(principal, "same", changed)
        changed = experiment()
        changed["chart_revision"]["title"] = "Changed immutable revision"
        with pytest.raises(FormSpaceError, match="chart_revision_conflict"):
            await repository.admit(principal, "new-key", changed)
        # A new repository/session pool represents restart; durable rows survive.
        restarted = FormSpaceRepository(RetentionRepository(sessions, RetentionConfig(enabled=True)), Principal)
        assert (await restarted.get(principal, job_id))["state"] == "admitted"
        for denied in (bob, other_project):
            assert await restarted.list(denied) == []
            with pytest.raises(FormSpaceError, match="job_not_found"):
                await restarted.get(denied, job_id)
        claim = await restarted.claim("fixture-worker-1")
        assert claim["job"]["job_id"] == job_id
        lease = {"lease_token": claim["lease"]["token"], "fence": claim["lease"]["fence"]}
        assert await restarted.claim("fixture-worker-2") is None
        async with sessions() as session, session.begin():
            await session.execute(text("UPDATE formspace.outbox SET lease_until=now()-interval '1 second' WHERE job_id=:id"), {"id": job_id})
        recovered = await restarted.claim("fixture-worker-2")
        assert recovered["lease"]["fence"] > lease["fence"]
        with pytest.raises(FormSpaceError, match="lease_lost"):
            await restarted.heartbeat(job_id, lease)
        lease = {"lease_token": recovered["lease"]["token"], "fence": recovered["lease"]["fence"]}
        # Regression: a membership lock can outlive the lease. Transaction-start
        # now() is insufficient: the post-lock clock_timestamp() fence must win.
        async with sessions() as session, session.begin():
            await session.execute(text("""UPDATE formspace.outbox SET
                lease_until=clock_timestamp()+interval '0.2 second' WHERE job_id=:id"""), {"id": job_id})
        async with sessions() as blocker, blocker.begin():
            await blocker.execute(text("UPDATE retention.membership SET active=true WHERE subject='alice'"))
            waiting = asyncio.create_task(restarted.heartbeat(job_id, lease))
            await asyncio.sleep(0.35)
            assert not waiting.done()
        with pytest.raises(FormSpaceError, match="lease_lost"):
            await waiting
        await restarted.cancel(principal, job_id)
        with pytest.raises(FormSpaceError, match="lease_lost"):
            await restarted.computed(job_id, lease, b"{}", "a" * 64)
        async with sessions() as session, session.begin():
            await session.execute(text("UPDATE retention.membership SET active=false WHERE subject='alice'"))
        with pytest.raises(RetentionError, match="membership_required"):
            await restarted.get(principal, job_id)
    finally:
        await engine.dispose()
