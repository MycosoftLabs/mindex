"""Offline snapshot contracts using real SQLite SELECTs and labeled fixture rows.

The thin adapter exercises the production read SQL, not PostgreSQL JSONB, auth,
the migration, network services, or physical data acquisition.
"""

import json
import sqlite3
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from mindex_api.routers.worldview import snapshots, response_envelope, avani_gateway
from mindex_api.worldview_snapshot_meta import snapshot_to_avani_meta, public_snapshot_meta


class SQLiteReads:
    def __init__(self, *, empty=False, missing_schema=False):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.statements = []
        self.commit_calls = 0
        self.connection.execute("ATTACH DATABASE ':memory:' AS worldview")
        if not missing_schema:
            self.connection.execute("""CREATE TABLE worldview.worldview_state_snapshots (
                snapshot_id TEXT PRIMARY KEY, captured_at TEXT, region TEXT,
                source_freshness TEXT, degraded INTEGER, confidence REAL,
                provenance TEXT, audit_trail_id TEXT, world_payload TEXT)""")
            if not empty:
                for key, captured, region in [
                    ("fixture-a", "2026-01-01T00:00:00+00:00", "A"),
                    ("fixture-b", "2026-02-01T00:00:00+00:00", "B"),
                ]:
                    self.connection.execute("""INSERT INTO worldview.worldview_state_snapshots
                        VALUES (?, ?, ?, ?, 0, 0.91, ?, ?, ?)""", (
                        key, captured, json.dumps({"label": region}),
                        json.dumps({"fixture-source": "source-reported-stale"}),
                        json.dumps({"source": "fixture-producer"}), "fixture-audit",
                        json.dumps({"private_payload": "must not reach public metadata"}),
                    ))
            self.connection.commit()
        self.connection.set_authorizer(self._authorize)

    def _authorize(self, operation, arg1, arg2, database, trigger):
        # SELECT reads are allowed; no read helper can initialize or mutate storage.
        if operation not in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        row = self.connection.execute(sql, params or {}).fetchone()
        mapped = dict(row) if row is not None else None
        if mapped:
            for key in ("region", "source_freshness", "provenance", "world_payload"):
                mapped[key] = json.loads(mapped[key])
        return SimpleNamespace(first=lambda: SimpleNamespace(_mapping=mapped) if mapped else None)

    async def commit(self):
        self.commit_calls += 1
        raise AssertionError("Snapshot read attempted commit")


@pytest.fixture
def db():
    db = SQLiteReads()
    yield db
    db.connection.close()


@pytest.mark.asyncio
async def test_real_sqlite_reads_select_only_and_preserve_identity(db):
    latest = await snapshots.get_latest_snapshot(db)
    first = await snapshots.get_snapshot(db, "fixture-a")
    assert latest["snapshot_id"] == "fixture-b"
    assert latest["region"] == {"label": "B"}
    assert first["snapshot_id"] == "fixture-a"
    assert first["region"] == {"label": "A"}
    assert await snapshots.get_snapshot(db, "not-present") is None
    assert len(db.statements) == 3
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in db.statements)
    assert db.commit_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("region", [{}, {"label": "A"}, {"lat": 0, "lng": 0, "radius_km": 2},
    {"lat_min": -1, "lat_max": 1, "lng_min": -1, "lng_max": 1}, {"lat": 0, "lon": 0, "radius_nm": 2}])
async def test_supplied_region_rejected_before_sql(db, region):
    with pytest.raises(HTTPException) as caught:
        await snapshots.get_latest_snapshot(db, region=region)
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "unsupported_snapshot_region"
    assert db.statements == []
    assert db.commit_calls == 0


def client_for(db):
    app = FastAPI()
    app.include_router(snapshots.router)
    app.dependency_overrides[snapshots.get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("region", ["A", "", '{"lat":0,"lng":0}'])
def test_direct_region_http_422_before_sql(db, region):
    with client_for(db) as client:
        response = client.get("/worldview/snapshots/latest", params={"region": region})
    assert response.status_code == 422
    assert db.statements == []


@pytest.mark.parametrize("suffix", ["latest", "absent-id"])
@pytest.mark.parametrize("missing_schema", [False, True])
def test_direct_missing_row_404_but_missing_schema_503(suffix, missing_schema):
    db = SQLiteReads(empty=True, missing_schema=missing_schema)
    try:
        with client_for(db) as client:
            response = client.get("/worldview/snapshots/" + suffix)
        assert response.status_code == (503 if missing_schema else 404)
        assert "worldview_state_snapshots" not in response.text
        assert db.commit_calls == 0
        assert all(sql.lstrip().upper().startswith("SELECT") for sql in db.statements)
    finally:
        db.connection.close()


@pytest.mark.asyncio
async def test_auxiliary_region_is_explicit_without_opening_database(monkeypatch):
    def forbidden_session():
        pytest.fail("unsupported region must not open a database session")
    monkeypatch.setattr(snapshots, "async_session_scope", forbidden_session)
    meta = await snapshots.get_latest_snapshot_meta(region={"label": "A"})
    assert meta["status"] == "unsupported_region"
    assert meta["worldstate_snapshot_id"] is None
    assert meta["freshness"] == "unknown"
    assert meta["confidence"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_schema", [False, True])
async def test_auxiliary_missing_and_unavailable_remain_distinct(monkeypatch, missing_schema):
    db = SQLiteReads(empty=True, missing_schema=missing_schema)
    @asynccontextmanager
    async def session_scope():
        yield db
    monkeypatch.setattr(snapshots, "async_session_scope", session_scope)
    try:
        meta = await snapshots.get_latest_snapshot_meta()
        assert meta["status"] == ("store_unavailable" if missing_schema else "missing")
        assert meta["worldstate_snapshot_id"] is None
        assert meta["confidence"] is None
        assert "no such table" not in str(meta)
        assert db.commit_calls == 0
    finally:
        db.connection.close()


@pytest.mark.asyncio
async def test_auxiliary_session_acquisition_failure_is_sanitized(monkeypatch):
    @asynccontextmanager
    async def failed_scope():
        raise RuntimeError("fixture-private session acquisition detail")
        yield  # pragma: no cover - marks the context-manager protocol
    monkeypatch.setattr(snapshots, "async_session_scope", failed_scope)
    meta = await snapshots.get_latest_snapshot_meta()
    assert meta["status"] == "store_unavailable"
    assert meta["worldstate_snapshot_id"] is None
    assert meta["confidence"] is None
    assert "fixture-private" not in str(meta)


@pytest.mark.asyncio
async def test_closed_database_is_sanitized_unavailable(db):
    db.connection.close()
    with pytest.raises(HTTPException) as caught:
        await snapshots.get_snapshot(db, "fixture-a")
    assert caught.value.status_code == 503
    assert caught.value.detail == {"code": "snapshot_store_unavailable", "message": "Worldview snapshot store unavailable"}


@pytest.mark.asyncio
async def test_old_snapshot_preserves_reported_evidence_without_freshness_claim(db):
    snapshot = await snapshots.get_snapshot(db, "fixture-a")
    meta = snapshot_to_avani_meta(snapshot)
    assert meta["status"] == "available"
    assert meta["captured_at"] == "2026-01-01T00:00:00+00:00"
    assert meta["source_freshness"] == {"fixture-source": "source-reported-stale"}
    assert meta["provenance"] == {"source": "fixture-producer"}
    assert meta["audit_trail_id"] == "fixture-audit"
    assert meta["reported_confidence"] == 0.91
    assert meta["reported_degraded"] is False
    assert meta["freshness"] == "unknown"
    assert meta["confidence"] is None
    assert meta["degraded"] is True
    assert "world_payload" not in meta
    assert "private_payload" not in str(meta)


@pytest.mark.asyncio
@pytest.mark.parametrize("review_fails", [False, True])
async def test_public_geo_data_kept_but_snapshot_cannot_be_replaced(monkeypatch, review_fails):
    def forbidden_session():
        pytest.fail("unsupported region must not look up a snapshot")
    async def review(**kwargs):
        assert kwargs["snapshot_meta"]["status"] == "unsupported_region"
        if review_fails:
            raise RuntimeError("fixture-private transport credential detail")
        return {"worldstate_snapshot_id": "unrelated", "freshness": "fresh", "degraded": False,
                "confidence": 0.7, "provenance": {"review": "fixture"}}
    monkeypatch.setattr(snapshots, "async_session_scope", forbidden_session)
    monkeypatch.setattr(avani_gateway, "review_worldview_response", review)
    data = [{"id": "fixture-scoped-domain-result"}]
    result = await response_envelope.wrap_governed_response(
        data=data, caller=SimpleNamespace(plan="fixture"), source_domains=["observations"],
        region={"lat": 0, "lng": 0, "radius_km": 2},
    )
    assert result["data"] == data
    assert result["meta"]["snapshot"]["status"] == "unsupported_region"
    assert result["meta"]["avani"]["worldstate_snapshot_id"] is None
    assert result["meta"]["avani"]["freshness"] == "unknown"
    assert result["meta"]["avani"]["degraded"] is True
    assert "fixture-private" not in str(result)
    if not review_fails:
        assert result["meta"]["avani"]["confidence"] == 0.7
        assert result["meta"]["avani"]["confidence_scope"] == "governance_review"


@pytest.mark.asyncio
async def test_equal_capture_times_have_deterministic_id_order(db):
    # Explicit fixture setup, before read-only enforcement resumes.
    db.connection.set_authorizer(None)
    db.connection.execute("UPDATE worldview.worldview_state_snapshots SET captured_at = '2026-01-01T00:00:00+00:00'")
    db.connection.commit()
    db.connection.set_authorizer(db._authorize)
    assert (await snapshots.get_latest_snapshot(db))["snapshot_id"] == "fixture-b"
    assert db.commit_calls == 0


def test_public_metadata_is_allowlisted_and_internal_source_map_is_not_exposed():
    internal = snapshot_to_avani_meta({
        "snapshot_id": "fixture", "source_freshness": {"internal-device": {"state": "claimed-fresh"}},
        "provenance": {"source": "fixture-producer"},
        "world_payload": {"secret": "fixture-private"}, "summary_payload": {"private": True},
    })
    public = public_snapshot_meta({**internal, "unexpected_private_field": "fixture-private"})
    assert internal["source_freshness"] == {"internal-device": {"state": "claimed-fresh"}}
    assert public["source_freshness_available"] is True
    assert public["source_freshness_assessment"] == "unknown"
    assert public["reported_degraded"] is None
    assert public["reported_confidence"] is None
    assert "internal-device" not in str(public)
    assert "fixture-private" not in str(public)
    assert "source_freshness" not in public
    assert public["evidence_status"] == "source_reported_unverified"


@pytest.mark.asyncio
async def test_public_global_snapshot_preserves_evidence_without_publishing_source_map(db, monkeypatch):
    @asynccontextmanager
    async def session_scope():
        yield db
    async def review(**kwargs):
        assert kwargs["snapshot_meta"]["status"] == "available"
        return {"freshness": "fresh", "worldstate_snapshot_id": "unrelated", "degraded": False,
                "confidence": 0.7, "governance_notes": ["AVANI review unavailable; raw private failure detail"]}
    monkeypatch.setattr(snapshots, "async_session_scope", session_scope)
    monkeypatch.setattr(avani_gateway, "review_worldview_response", review)
    result = await response_envelope.wrap_governed_response(
        data=[{"id": "fixture"}], caller=SimpleNamespace(plan="fixture"), source_domains=["taxa"],
    )
    assert result["meta"]["snapshot"]["status"] == "available"
    assert result["meta"]["snapshot"]["captured_at"] == "2026-02-01T00:00:00+00:00"
    assert result["meta"]["avani"]["worldstate_snapshot_id"] == "fixture-b"
    assert result["meta"]["avani"]["freshness"] == "unknown"
    assert "fixture-source" not in str(result)
    assert "raw private failure" not in str(result)
    assert "private_payload" not in str(result)
    assert db.commit_calls == 0


@pytest.mark.parametrize("failure", [None, "execute", "commit"])
def test_explicit_post_has_no_schema_setup_and_commit_failure_is_not_success(failure):
    class RecordingWriter:
        def __init__(self):
            self.statements = []
            self.commits = 0

        async def execute(self, sql, params=None):
            self.statements.append(str(sql).strip())
            if failure == "execute":
                raise RuntimeError("fixture-private database detail")
            return SimpleNamespace(first=lambda: SimpleNamespace(_mapping={"snapshot_id": "fixture-write"}))

        async def commit(self):
            self.commits += 1
            if failure == "commit":
                raise RuntimeError("fixture-private commit detail")

    writer = RecordingWriter()
    with client_for(writer) as client:
        response = client.post("/worldview/snapshots", json={
            "snapshot_id": "fixture-write", "captured_at": "2026-01-01T00:00:00+00:00",
        })
    assert response.status_code == (200 if failure is None else 503)
    assert all(sql.upper().startswith(("INSERT", "SELECT")) for sql in writer.statements)
    assert writer.commits == (0 if failure == "execute" else 1)
    assert "fixture-private" not in response.text
