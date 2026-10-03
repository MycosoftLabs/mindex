"""Opt-in disposable LOCAL PostgreSQL migration/locking qualification.

Never consumes MINDEX_DB_DSN or production settings. Caller must create an empty
local database named ledger_fixture_* and set LEDGER_TEST_POSTGRES_DSN explicitly.
No fixture database is dropped by this test; retain it for inspection.
"""
import os
import re
import json
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mindex_api.ledger import provenance as p
from test_ledger_provenance import OWNER, OPERATOR, request_for, key_for, ARTIFACT_ID, ARTIFACT_HASH

FIXTURE_DATABASE = re.compile(r"ledger_fixture_[0-9a-f]{32}\Z")


def local_fixture_target(dsn: str):
    """Require a unique per-run database name and a loopback-only URL."""
    try:
        target = urlsplit(dsn)
        database = target.path.removeprefix("/")
        port = target.port  # Force malformed ports to fail before any connection.
    except ValueError as exc:
        raise ValueError("Invalid disposable PostgreSQL DSN") from exc
    if (target.scheme != "postgresql" or target.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not FIXTURE_DATABASE.fullmatch(database) or target.query or target.fragment
            or target.username is None):
        raise ValueError("Only a unique loopback ledger_fixture_<uuidhex> database is permitted")
    return target, database, port


@pytest.mark.parametrize("dsn", [
    "postgresql://fixture@db.example/ledger_fixture_0123456789abcdef0123456789abcdef",
    "postgresql://fixture@127.0.0.1/ledger_fixture_shared",
    "postgresql://fixture@127.0.0.1/ledger_fixture_0123456789abcdef0123456789abcdef?sslmode=require",
    "postgresql://fixture@127.0.0.1:bad/ledger_fixture_0123456789abcdef0123456789abcdef",
])
def test_postgres_gate_rejects_nonlocal_nonunique_or_ambiguous_database(dsn):
    with pytest.raises(ValueError):
        local_fixture_target(dsn)


def test_postgres_gate_accepts_only_a_uuid_named_loopback_fixture():
    target, database, _ = local_fixture_target(
        "postgresql://fixture@127.0.0.1:55439/ledger_fixture_0123456789abcdef0123456789abcdef")
    assert target.hostname == "127.0.0.1"
    assert database == "ledger_fixture_0123456789abcdef0123456789abcdef"


@pytest.mark.asyncio
async def test_postgres_migration_append_only_engine_reopen_and_concurrent_transition():
    dsn = os.environ.get("LEDGER_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Disposable local PostgreSQL unavailable; migration/locking qualification pending")
    try:
        target, database, _ = local_fixture_target(dsn)
    except ValueError as exc:
        pytest.fail(str(exc))
    asyncpg = pytest.importorskip("asyncpg")
    connection = await asyncpg.connect(dsn, timeout=5)
    try:
        identity = await connection.fetchrow("""
            SELECT current_database() AS database, host(inet_server_addr()) AS address,
                   current_user AS username, pg_get_userbyid(datdba) AS owner
              FROM pg_database WHERE datname = current_database()
        """)
        assert identity["database"] == database
        assert identity["address"] in {"127.0.0.1", "::1"}, "Fixture must use a loopback TCP listener"
        assert identity["owner"] == identity["username"], "Fixture database must be owned by its test user"
        assert not await connection.fetchval("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='ledger')"), \
            "Fixture database must be empty: existing ledger schema preserved"
        assert not await connection.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
                 WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                   AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
            )
        """), "Fixture database must contain no user tables, views, sequences or foreign tables"
        migration = Path(__file__).parents[1] / "migrations" / "20261001_ledger_provenance.sql"
        await connection.execute(migration.read_text(encoding="utf-8"))
    finally:
        await connection.close()
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            row = await p.register(db, OWNER, request_for())
            row = await p.validate(db, OWNER, row["id"], p.ValidateRequest(idempotency_key="pg-validate"),
                                   trusted_key=key_for(), artifact_hashes={ARTIFACT_ID: ARTIFACT_HASH})
            for sql in ("UPDATE ledger.provenance_event SET kind='tampered'",
                        "DELETE FROM ledger.provenance_event",
                        "UPDATE ledger.provenance_record SET content_hash=repeat('f',64),version=version+1"):
                with pytest.raises(DBAPIError):
                    await db.execute(text(sql))
                await db.rollback()
        # Separate sessions exercise real row lock contention + idempotent replay.
        import asyncio
        approval = p.ApprovalRequest(idempotency_key="pg-approve", policy_version="fixture-pg-v1",
                                     privacy_review="accepted", equality_leak_review="accepted")
        async def approve():
            async with factory() as db:
                return await p.approve(db, OPERATOR, row["id"], approval)
        results = await asyncio.gather(approve(), approve())
        assert all(item["state"] == "approved" for item in results)
        await engine.dispose()
        async with factory() as db:
            assert len(await p.list_events(db, OWNER, row["id"])) == 3
            assert (await p.list_queue(db, OPERATOR))[0]["status"] == "manual_submission_disabled"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_z_owned_server_process_restart_readback(monkeypatch):
    """Stop/relaunch only the explicitly task-owned local cluster, then read via ASGI."""
    import asyncio
    dsn = os.environ.get("LEDGER_TEST_POSTGRES_DSN")
    data_value = os.environ.get("LEDGER_TEST_POSTGRES_DATA_DIR")
    pg_ctl_value = os.environ.get("LEDGER_TEST_POSTGRES_PG_CTL")
    if not dsn or not data_value or not pg_ctl_value:
        pytest.skip("Task-owned process restart requires explicit DSN, data directory, and pg_ctl paths")
    target, database, port = local_fixture_target(dsn)
    if target.hostname not in {"127.0.0.1", "localhost"} or not port:
        pytest.fail("Process restart qualification requires explicit IPv4 loopback host and port")
    import tempfile
    data_dir = Path(data_value).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        data_dir.relative_to(temp_root)
    except ValueError:
        pytest.fail("PostgreSQL data directory must be inside the local temporary directory")
    if not data_dir.name.startswith("codex-brief10-ledger-pg-") or not (data_dir / "PG_VERSION").is_file():
        pytest.fail("PostgreSQL data directory is not the uniquely named Brief10 fixture")
    pg_ctl = Path(pg_ctl_value).resolve()
    postgres_executable = pg_ctl.with_name("postgres.exe" if os.name == "nt" else "postgres")
    restart_log = data_dir / "brief10-process-restart.log"
    if not pg_ctl.is_file() or not postgres_executable.is_file():
        pytest.fail("PostgreSQL process-control runtime is incomplete")

    asyncpg = pytest.importorskip("asyncpg")
    sqlalchemy_dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(sqlalchemy_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    server_stopped = False
    try:
        async with factory() as db:
            candidates = [row for row in await p.list_records(db, OWNER, limit=100)
                          if row["state"] == "approved"]
            assert len(candidates) == 1, "Restart fixture must contain one approved Brief10 record"
            expected = candidates[0]
            events_before = await p.list_events(db, OWNER, expected["id"])
            assert [row["kind"] for row in events_before] == ["register", "validate", "approve"]
        await engine.dispose()

        pid_file = data_dir / "postmaster.pid"
        if not pid_file.is_file():
            pytest.fail("Task-owned PostgreSQL server is not running")
        old_pid = int(pid_file.read_text(encoding="ascii").splitlines()[0])
        if os.name == "nt":
            ps = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {old_pid}'; "
                "if ($null -eq $p) { exit 2 }; "
                "[ordered]@{pid=$p.ProcessId; exe=$p.ExecutablePath; command=$p.CommandLine} "
                "| ConvertTo-Json -Compress"], check=True, capture_output=True, text=True, timeout=10)
            process = json.loads(ps.stdout)
            command = process["command"].replace("\\", "/").casefold()
            data_token = str(data_dir).replace("\\", "/").casefold()
            assert Path(process["exe"]).resolve() == postgres_executable
            assert data_token in command and str(old_pid) == str(process["pid"])
        else:
            process_exe = Path(f"/proc/{old_pid}/exe").resolve()
            command = Path(f"/proc/{old_pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
            assert process_exe == postgres_executable
            assert str(data_dir) in command
        assert "retention-pgdata" not in str(data_dir).casefold()
        assert "retention-fixture-pgdata" not in str(data_dir).casefold()

        subprocess.run([str(pg_ctl), "-D", str(data_dir), "-m", "fast", "-w", "stop"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        server_stopped = True
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        subprocess.run([str(pg_ctl), "-D", str(data_dir), "-l", str(restart_log),
                        "-o", f"-h 127.0.0.1 -p {port} -c listen_addresses=127.0.0.1", "-w", "start"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        server_stopped = False

        new_pid = int((data_dir / "postmaster.pid").read_text(encoding="ascii").splitlines()[0])
        if os.name == "nt":
            ps = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {new_pid}'; "
                "if ($null -eq $p) { exit 2 }; "
                "[ordered]@{pid=$p.ProcessId; exe=$p.ExecutablePath; command=$p.CommandLine} "
                "| ConvertTo-Json -Compress"], check=True, capture_output=True, text=True, timeout=10)
            process = json.loads(ps.stdout)
            command = process["command"].replace("\\", "/").casefold()
            assert Path(process["exe"]).resolve() == postgres_executable
            assert str(data_dir).replace("\\", "/").casefold() in command
        else:
            assert Path(f"/proc/{new_pid}/exe").resolve() == postgres_executable
            assert str(data_dir) in Path(f"/proc/{new_pid}/cmdline").read_bytes().decode(errors="replace")

        identity = await asyncpg.connect(dsn, timeout=5)
        try:
            after = await identity.fetchrow("""
                SELECT current_database() AS database, host(inet_server_addr()) AS address,
                       current_user AS username, pg_get_userbyid(datdba) AS owner
                  FROM pg_database WHERE datname = current_database()
            """)
            assert after["database"] == database
            assert after["address"] == "127.0.0.1"
            assert after["owner"] == after["username"] == target.username
        finally:
            await identity.close()

        # Reopen through MINDEX's real HTTP router and SQL repository after the
        # PostgreSQL server process has exited and relaunched.
        import httpx
        from fastapi import FastAPI, HTTPException
        from types import SimpleNamespace
        from mindex_api import provenance_access as access
        from mindex_api.dependencies import get_db_session
        from mindex_api.routers.provenance import router
        from test_provenance_api import PREFIX

        async def session():
            async with factory() as db:
                yield db

        async def authenticate(request):
            if (request.headers.get("authorization") != "Bearer task-owned-restart-fixture"
                    or request.headers.get("x-tenant-id") != OWNER.tenant_id
                    or request.headers.get("x-project-id") != OWNER.project_id):
                raise HTTPException(403, "fixture_scope_denied")
            return OWNER

        monkeypatch.setattr(access, "retention_module", lambda: SimpleNamespace(require_principal=authenticate))
        app = FastAPI()
        app.include_router(router, prefix="/api/mindex")
        app.dependency_overrides[get_db_session] = session
        headers = {"Authorization": "Bearer task-owned-restart-fixture",
                   "X-Tenant-Id": OWNER.tenant_id, "X-Project-Id": OWNER.project_id}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            async with asyncio.timeout(10):
                detail = await client.get(f"{PREFIX}/records/{expected['id']}", headers=headers)
                audit = await client.get(f"{PREFIX}/records/{expected['id']}/events", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["state"] == "approved"
        assert detail.json()["content_hash"] == expected["content_hash"]
        assert detail.json()["onchain_confirmed"] is False
        assert audit.status_code == 200, audit.text
        assert [row["kind"] for row in audit.json()] == [row["kind"] for row in events_before]
        await engine.dispose()
    finally:
        await engine.dispose()
        if server_stopped:
            subprocess.run([str(pg_ctl), "-D", str(data_dir), "-l", str(restart_log),
                            "-o", f"-h 127.0.0.1 -p {port} -c listen_addresses=127.0.0.1", "-w", "start"],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
