"""
SupabaseSyncWorker — keep the Supabase cloud mirror in step with Postgres.
=========================================================================
The canonical store is local Postgres (hot). Supabase is the *warm* mirror that
the public website, the Earth Simulator, and the WorldView API read from
globally. This worker incrementally pushes new/updated rows from a configured
set of local tables up to Supabase via PostgREST, tracking progress in
``app.supabase_sync_ledger`` so each run only moves the delta.

Design goals:
- **Incremental**: per-table high-water mark on a timestamp column.
- **Self-describing**: the watermark column is auto-detected from a candidate
  list, so we don't hard-code columns that may differ per table.
- **Tolerant**: a missing local table, a missing Supabase table (PostgREST 404),
  or absent credentials disables just that table — never the whole run.
- **Self-contained**: depends only on psycopg + httpx (already required), not on
  ``mindex_api``, so it can run inside the lean ETL container.

Configure which tables sync via ``MINDEX_SUPABASE_SYNC_TABLES`` (comma list of
``local_schema.table:supabase_table:conflict_col``) or rely on the defaults.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mindex.sync.supabase")

# Candidate "last changed" columns, most-specific first.
_WATERMARK_CANDIDATES = (
    "updated_at",
    "synced_at",
    "ingested_at",
    "created_at",
    "observed_at",
    "occurred_at",
    "detected_at",
    "last_seen",
)

_BATCH = int(os.getenv("MINDEX_SUPABASE_SYNC_BATCH", "500"))
_MAX_BATCHES = int(os.getenv("MINDEX_SUPABASE_SYNC_MAX_BATCHES", "10"))


@dataclass
class TableSync:
    local_table: str           # e.g. "core.taxon"
    supabase_table: str        # e.g. "mindex_taxa"
    conflict_col: str = "id"
    watermark_col: Optional[str] = None  # auto-detected if None
    select_cols: str = "*"
    extra_filter: str = ""     # raw SQL appended to WHERE (advanced)


def _default_tables() -> List[TableSync]:
    raw = os.getenv("MINDEX_SUPABASE_SYNC_TABLES", "").strip()
    if raw:
        tables: List[TableSync] = []
        for spec in raw.split(","):
            parts = [p.strip() for p in spec.split(":") if p.strip()]
            if len(parts) >= 2:
                tables.append(
                    TableSync(
                        local_table=parts[0],
                        supabase_table=parts[1],
                        conflict_col=parts[2] if len(parts) > 2 else "id",
                    )
                )
        if tables:
            return tables
    # Sensible defaults — the hot rows the web tier needs globally.
    return [
        TableSync("core.taxon", "mindex_taxa", "id"),
        TableSync("obs.observation", "mindex_observations", "id"),
        TableSync("earth.earthquakes", "mindex_earthquakes", "source_id"),
        TableSync("earth.wildfires", "mindex_wildfires", "source_id"),
    ]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SupabaseSyncWorker:
    """Push incremental deltas from Postgres to the Supabase mirror."""

    def __init__(self) -> None:
        self.url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
        self.enabled = bool(self.url and self.key)
        self.tables = _default_tables()
        self._json = None
        try:
            from psycopg.types.json import Json  # type: ignore

            self._json = Json
        except Exception:
            self._json = None

    # ------------------------------------------------------------------
    def run_once(self) -> int:
        if not self.enabled:
            logger.info("Supabase sync skipped — SUPABASE_URL / key not configured.")
            return 0
        try:
            import httpx  # noqa: F401
        except Exception as exc:  # pragma: no cover
            logger.warning("Supabase sync skipped — httpx unavailable: %s", exc)
            return 0

        total = 0
        for table in self.tables:
            try:
                total += self._sync_table(table)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Supabase sync for %s failed: %s", table.local_table, exc)
                self._record_ledger_error(table, str(exc))
        logger.info("Supabase sync pushed %d rows total.", total)
        return total

    # ------------------------------------------------------------------
    def _sync_table(self, table: TableSync) -> int:
        from ..db import db_session

        with db_session() as conn:
            watermark_col = table.watermark_col or self._detect_watermark(conn, table.local_table)
            if not watermark_col:
                logger.info(
                    "Supabase sync: %s has no timestamp column to track — skipped.",
                    table.local_table,
                )
                return 0
            last_value = self._read_ledger(conn, table.local_table)

        synced = 0
        cursor_value = last_value
        for _ in range(_MAX_BATCHES):
            rows, cursor_value = self._fetch_batch(table, watermark_col, cursor_value)
            if not rows:
                break
            if not self._push_to_supabase(table, rows):
                break  # destination unavailable; try again next cycle
            synced += len(rows)
            self._write_ledger(table.local_table, cursor_value, len(rows))
            if len(rows) < _BATCH:
                break
        if synced:
            logger.info("Supabase sync: %s -> %s (%d rows)", table.local_table, table.supabase_table, synced)
        return synced

    def _detect_watermark(self, conn, local_table: str) -> Optional[str]:
        schema, _, name = local_table.partition(".")
        if not name:
            schema, name = "public", local_table
        try:
            cur = conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                """,
                (schema, name),
            )
            cols = {r["column_name"] for r in cur.fetchall()}
        except Exception:
            return None
        for candidate in _WATERMARK_CANDIDATES:
            if candidate in cols:
                return candidate
        return None

    def _fetch_batch(self, table: TableSync, watermark_col: str, last_value):
        from ..db import db_session

        where = f"{watermark_col} IS NOT NULL"
        params: List[Any] = []
        if last_value is not None:
            where += f" AND {watermark_col} > %s"
            params.append(last_value)
        if table.extra_filter:
            where += f" AND ({table.extra_filter})"

        sql = (
            f"SELECT {table.select_cols} FROM {table.local_table} "
            f"WHERE {where} ORDER BY {watermark_col} ASC LIMIT {_BATCH}"
        )
        with db_session() as conn:
            cur = conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
        cursor_value = last_value
        if rows:
            cursor_value = rows[-1].get(watermark_col, last_value)
        return rows, cursor_value

    def _push_to_supabase(self, table: TableSync, rows: List[Dict[str, Any]]) -> bool:
        import httpx

        records = [self._jsonable(r) for r in rows]
        try:
            resp = httpx.post(
                f"{self.url}/rest/v1/{table.supabase_table}",
                params={"on_conflict": table.conflict_col},
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
                json=records,
                timeout=30,
            )
            if resp.status_code in (404, 400, 401, 403):
                logger.info(
                    "Supabase table %s not writable (HTTP %s) — skipping this cycle.",
                    table.supabase_table,
                    resp.status_code,
                )
                return False
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Supabase push to %s failed: %s", table.supabase_table, exc)
            return False

    @staticmethod
    def _jsonable(row: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, datetime):
                out[key] = value.isoformat()
            elif isinstance(value, (bytes, bytearray, memoryview)):
                out[key] = bytes(value).hex()
            else:
                out[key] = value
        return out

    # ------------------------------------------------------------------
    # Ledger: app.supabase_sync_ledger tracks the high-water mark per table.
    # ------------------------------------------------------------------
    def _read_ledger(self, conn, table_name: str):
        try:
            cur = conn.execute(
                """
                SELECT last_synced_id FROM app.supabase_sync_ledger
                WHERE table_name = %s ORDER BY id DESC LIMIT 1
                """,
                (table_name,),
            )
            row = cur.fetchone()
            if row and row.get("last_synced_id"):
                return row["last_synced_id"]
        except Exception:
            conn.rollback()
        return None

    def _write_ledger(self, table_name: str, cursor_value, count: int) -> None:
        from ..db import db_session

        try:
            with db_session() as conn:
                conn.execute(
                    """
                    INSERT INTO app.supabase_sync_ledger
                        (table_name, last_synced_at, last_synced_id, records_synced,
                         sync_direction, status)
                    VALUES (%s, NOW(), %s, %s, 'push', 'ok')
                    """,
                    (table_name, str(cursor_value) if cursor_value is not None else None, count),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("ledger write for %s failed: %s", table_name, exc)

    def _record_ledger_error(self, table: TableSync, message: str) -> None:
        from ..db import db_session

        try:
            with db_session() as conn:
                conn.execute(
                    """
                    INSERT INTO app.supabase_sync_ledger
                        (table_name, last_synced_at, records_synced, sync_direction,
                         status, error_message)
                    VALUES (%s, NOW(), 0, 'push', 'error', %s)
                    """,
                    (table.local_table, message[:500]),
                )
        except Exception:
            pass


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    synced = SupabaseSyncWorker().run_once()
    print(f"Supabase sync pushed {synced} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
