from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from .config import settings


_engine: Optional[AsyncEngine] = None
_session_factory: Optional[sessionmaker] = None


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
