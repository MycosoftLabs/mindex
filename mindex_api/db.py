from __future__ import annotations

from typing import Optional

import asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from .config import settings


_engine: Optional[AsyncEngine] = None
_session_factory: Optional[sessionmaker] = None
_asyncpg_pool: Optional[asyncpg.Pool] = None


def _ensure_engine() -> None:
    """Create the SQLAlchemy engine lazily.

    This keeps import-time side effects minimal and allows tests (that override the DB
    dependency) to run without requiring the async DB driver to be installed.
    """
    global _engine, _session_factory
    if _engine is not None and _session_factory is not None:
        return

    _engine = create_async_engine(
        settings.mindex_db_dsn,
        future=True,
        echo=False,
    )
    _session_factory = sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db():
    _ensure_engine()
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session


async def get_db_pool() -> asyncpg.Pool:
    """Get an asyncpg connection pool for raw SQL queries (used by FCI router).

    Lazily creates the pool on first call and reuses it for subsequent calls.
    The DSN is derived from the SQLAlchemy DSN by replacing the dialect prefix.
    """
    global _asyncpg_pool
    if _asyncpg_pool is None:
        # Convert SQLAlchemy DSN (postgresql+asyncpg://...) to plain postgres DSN
        dsn = str(settings.mindex_db_dsn)
        if dsn.startswith("postgresql+asyncpg://"):
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        elif dsn.startswith("sqlite"):
            raise RuntimeError("FCI router requires PostgreSQL; SQLite is not supported")
        _asyncpg_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    return _asyncpg_pool
