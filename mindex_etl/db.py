from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from .config import settings


def get_connection() -> Connection:
    return psycopg.connect(settings.database_url, row_factory=dict_row)


@contextmanager
def db_session() -> Iterator[Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

