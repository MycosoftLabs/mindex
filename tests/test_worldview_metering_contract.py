from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

from mindex_api import db as database
from mindex_api.middleware.metering import MeteringMiddleware


@pytest.mark.asyncio
async def test_usage_transaction_binds_postgresql_parameters_and_commits(monkeypatch):
    calls = []

    async def execute(statement, params):
        compiled = statement.compile(dialect=PGDialect_asyncpg())
        # Constructing parameters fails if SQLAlchemy parsed a truncated bind name.
        bound = compiled.construct_params(params)
        assert set(bound) == set(params)
        assert ":key_id" not in str(compiled)
        assert ":ip" not in str(compiled)
        assert ":meta" not in str(compiled)
        calls.append((str(compiled), bound))

    session = type("FakeSession", (), {})()
    session.execute = AsyncMock(side_effect=execute)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    async def get_db():
        yield session

    monkeypatch.setattr(database, "get_db", get_db)
    key_id = str(uuid4())
    await MeteringMiddleware(None)._record_usage(
        key_id=key_id, endpoint="/api/worldview/v1/search", method="GET",
        status_code=200, elapsed_ms=25, ip_address="127.0.0.1", user_agent="audit-test",
    )
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    assert len(calls) == 3
    assert all(params["key_id"] == key_id for _, params in calls)
    assert calls[1][1]["window_start"].second == 0
    assert json.loads(calls[2][1]["meta"]) == {"method": "GET", "status_code": 200, "elapsed_ms": 25}


@pytest.mark.asyncio
async def test_usage_transaction_rolls_back_on_storage_failure(monkeypatch):
    session = type("FakeSession", (), {})()
    session.execute = AsyncMock(side_effect=RuntimeError("fixture database unavailable"))
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    async def get_db():
        yield session

    monkeypatch.setattr(database, "get_db", get_db)
    await MeteringMiddleware(None)._record_usage(
        key_id=str(uuid4()), endpoint="/api/worldview/v1/search", method="GET",
        status_code=200, elapsed_ms=25, ip_address=None, user_agent=None,
    )
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
