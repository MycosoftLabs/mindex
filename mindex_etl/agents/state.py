"""
AgentStateStore — durable state for the orchestrator.
=====================================================
Persists agent registry + live state, run history, the orchestrator heartbeat,
and backup history to the ``etl.*`` schema using the project's synchronous
psycopg session (`mindex_etl.db.db_session`).

Everything degrades gracefully: if Postgres is unreachable the store flips to a
no-op/in-memory mode and the orchestrator keeps running (it just loses
cross-restart persistence until the DB comes back). No method ever raises.
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mindex.agents.state")

# Mirrors migrations/20260609_mindex_agent_runtime_jun09_2026.sql so the runtime
# is self-healing on databases where that migration hasn't been applied yet.
_ENSURE_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS etl;

CREATE TABLE IF NOT EXISTS etl.source_agent (
    name TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'source',
    concurrency_group TEXT NOT NULL DEFAULT 'default',
    description TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    priority INTEGER NOT NULL DEFAULT 100,
    schedule_seconds INTEGER NOT NULL DEFAULT 86400,
    max_pages INTEGER,
    domain_mode TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    last_run_at TIMESTAMPTZ,
    last_finished_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    cooldown_until TIMESTAMPTZ,
    last_status TEXT,
    last_records INTEGER NOT NULL DEFAULT 0,
    last_duration_ms INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_runs BIGINT NOT NULL DEFAULT 0,
    total_records BIGINT NOT NULL DEFAULT 0,
    watermark JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_source_agent_due ON etl.source_agent (enabled, next_run_at);

CREATE TABLE IF NOT EXISTS etl.agent_run (
    id BIGSERIAL PRIMARY KEY,
    agent_name TEXT NOT NULL,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    records INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    error TEXT,
    host TEXT,
    pid INTEGER,
    cycle BIGINT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_agent_run_agent_time ON etl.agent_run (agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_run_time ON etl.agent_run (started_at DESC);

CREATE TABLE IF NOT EXISTS etl.orchestrator_heartbeat (
    id INTEGER PRIMARY KEY DEFAULT 1,
    host TEXT,
    pid INTEGER,
    started_at TIMESTAMPTZ,
    last_beat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cycle BIGINT NOT NULL DEFAULT 0,
    agents_total INTEGER NOT NULL DEFAULT 0,
    agents_enabled INTEGER NOT NULL DEFAULT 0,
    agents_running INTEGER NOT NULL DEFAULT 0,
    max_concurrency INTEGER NOT NULL DEFAULT 0,
    stats JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS etl.backup_log (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    target TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    size_bytes BIGINT,
    object_count BIGINT,
    storage_class TEXT,
    error TEXT,
    host TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_backup_log_time ON etl.backup_log (started_at DESC);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentStateStore:
    """Durable persistence for orchestrator state (psycopg, synchronous)."""

    def __init__(self) -> None:
        self.host = socket.gethostname()
        self.pid = os.getpid()
        self.available = False
        self._json = None
        try:  # psycopg Json adapter — only import if psycopg is present
            from psycopg.types.json import Json  # type: ignore

            self._json = Json
        except Exception:  # pragma: no cover - psycopg always present in runtime
            self._json = None

    # ------------------------------------------------------------------
    def _session(self):
        from ..db import db_session  # local import keeps base importable w/o psycopg

        return db_session()

    def _wrap_json(self, value: Any):
        if self._json is not None:
            return self._json(value)
        return value

    # ------------------------------------------------------------------
    def ensure_schema(self) -> bool:
        """Create the etl.* schema/tables if missing. Returns availability."""
        try:
            with self._session() as conn:
                conn.execute(_ENSURE_SCHEMA_SQL)
            self.available = True
            logger.info("etl.* schema ready (state persistence ON)")
        except Exception as exc:  # noqa: BLE001
            self.available = False
            logger.warning(
                "Agent state persistence DISABLED (DB unavailable: %s). "
                "Runtime continues in-memory.",
                exc,
            )
        return self.available

    # ------------------------------------------------------------------
    def upsert_agent(self, agent) -> None:
        """Register an agent and its config. Live-state columns are only set on
        first insert so a restart doesn't clobber resumed state."""
        if not self.available:
            return
        s = agent.to_state_dict()
        try:
            with self._session() as conn:
                conn.execute(
                    """
                    INSERT INTO etl.source_agent
                        (name, source, kind, concurrency_group, description,
                         enabled, priority, schedule_seconds, max_pages, domain_mode,
                         next_run_at, updated_at)
                    VALUES
                        (%(name)s, %(source)s, %(kind)s, %(concurrency_group)s, %(description)s,
                         %(enabled)s, %(priority)s, %(schedule_seconds)s, %(max_pages)s, %(domain_mode)s,
                         %(next_run_at)s, NOW())
                    ON CONFLICT (name) DO UPDATE SET
                        source = EXCLUDED.source,
                        kind = EXCLUDED.kind,
                        concurrency_group = EXCLUDED.concurrency_group,
                        description = EXCLUDED.description,
                        priority = EXCLUDED.priority,
                        schedule_seconds = EXCLUDED.schedule_seconds,
                        max_pages = EXCLUDED.max_pages,
                        domain_mode = EXCLUDED.domain_mode,
                        updated_at = NOW()
                    """,
                    s,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("upsert_agent(%s) failed: %s", agent.name, exc)

    def load_state(self, agent) -> None:
        """Hydrate one agent's live state from the DB (post-restart resume)."""
        if not self.available:
            return
        try:
            with self._session() as conn:
                cur = conn.execute(
                    "SELECT * FROM etl.source_agent WHERE name = %s", (agent.name,)
                )
                row = cur.fetchone()
            if row:
                agent.load_state_dict(dict(row))
        except Exception as exc:  # noqa: BLE001
            logger.debug("load_state(%s) failed: %s", agent.name, exc)

    def save_state(self, agent) -> None:
        """Persist an agent's live state after a run / schedule change."""
        if not self.available:
            return
        s = agent.to_state_dict()
        s["watermark"] = self._wrap_json(s.get("watermark") or {})
        try:
            with self._session() as conn:
                conn.execute(
                    """
                    UPDATE etl.source_agent SET
                        enabled = %(enabled)s,
                        status = %(status)s,
                        last_run_at = %(last_run_at)s,
                        last_finished_at = %(last_finished_at)s,
                        next_run_at = %(next_run_at)s,
                        cooldown_until = %(cooldown_until)s,
                        last_status = %(last_status)s,
                        last_records = %(last_records)s,
                        last_duration_ms = %(last_duration_ms)s,
                        last_error = %(last_error)s,
                        consecutive_failures = %(consecutive_failures)s,
                        total_runs = %(total_runs)s,
                        total_records = %(total_records)s,
                        watermark = %(watermark)s,
                        updated_at = NOW()
                    WHERE name = %(name)s
                    """,
                    s,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("save_state(%s) failed: %s", agent.name, exc)

    def refresh_control_flags(self, agent) -> Optional[str]:
        """Re-read operator-controlled fields (enabled, forced next_run_at) so
        API pause/resume/run-now take effect without restarting the runtime.
        Returns 'run_now' if the agent was nudged to run immediately."""
        if not self.available:
            return None
        try:
            with self._session() as conn:
                cur = conn.execute(
                    "SELECT enabled, next_run_at, cooldown_until FROM etl.source_agent WHERE name = %s",
                    (agent.name,),
                )
                row = cur.fetchone()
            if not row:
                return None
            agent.enabled = bool(row["enabled"])
            forced = None
            db_next = row.get("next_run_at")
            # If an operator pushed next_run_at into the past (run-now), honor it.
            if db_next is not None and (agent.next_run_at is None or db_next < agent.next_run_at):
                agent.next_run_at = db_next
                if db_next <= _utcnow():
                    agent.cooldown_until = None
                    forced = "run_now"
            return forced
        except Exception as exc:  # noqa: BLE001
            logger.debug("refresh_control_flags(%s) failed: %s", agent.name, exc)
            return None

    # ------------------------------------------------------------------
    def record_run_start(self, agent, cycle: int) -> Optional[int]:
        if not self.available:
            return None
        try:
            with self._session() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO etl.agent_run (agent_name, source, status, host, pid, cycle)
                    VALUES (%s, %s, 'running', %s, %s, %s)
                    RETURNING id
                    """,
                    (agent.name, agent.source, self.host, self.pid, cycle),
                )
                row = cur.fetchone()
                return int(row["id"]) if row else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("record_run_start(%s) failed: %s", agent.name, exc)
            return None

    def record_run_finish(self, run_id: Optional[int], result) -> None:
        if not self.available or run_id is None:
            return
        try:
            with self._session() as conn:
                conn.execute(
                    """
                    UPDATE etl.agent_run SET
                        status = %s, records = %s, duration_ms = %s,
                        error = %s, finished_at = %s
                    WHERE id = %s
                    """,
                    (
                        result.status,
                        int(result.records or 0),
                        int(result.duration_ms or 0),
                        result.error,
                        result.finished_at or _utcnow(),
                        run_id,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("record_run_finish(%s) failed: %s", run_id, exc)

    # ------------------------------------------------------------------
    def write_heartbeat(self, snapshot: Dict[str, Any]) -> None:
        if not self.available:
            return
        payload = dict(snapshot)
        payload["host"] = self.host
        payload["pid"] = self.pid
        payload["stats"] = self._wrap_json(payload.get("stats") or {})
        try:
            with self._session() as conn:
                conn.execute(
                    """
                    INSERT INTO etl.orchestrator_heartbeat
                        (id, host, pid, started_at, last_beat_at, cycle,
                         agents_total, agents_enabled, agents_running, max_concurrency, stats)
                    VALUES
                        (1, %(host)s, %(pid)s, %(started_at)s, NOW(), %(cycle)s,
                         %(agents_total)s, %(agents_enabled)s, %(agents_running)s,
                         %(max_concurrency)s, %(stats)s)
                    ON CONFLICT (id) DO UPDATE SET
                        host = EXCLUDED.host, pid = EXCLUDED.pid,
                        started_at = EXCLUDED.started_at, last_beat_at = NOW(),
                        cycle = EXCLUDED.cycle, agents_total = EXCLUDED.agents_total,
                        agents_enabled = EXCLUDED.agents_enabled,
                        agents_running = EXCLUDED.agents_running,
                        max_concurrency = EXCLUDED.max_concurrency,
                        stats = EXCLUDED.stats
                    """,
                    payload,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("write_heartbeat failed: %s", exc)

    # ------------------------------------------------------------------
    def record_backup(
        self,
        kind: str,
        *,
        target: Optional[str] = None,
        status: str = "running",
        size_bytes: Optional[int] = None,
        object_count: Optional[int] = None,
        storage_class: Optional[str] = None,
        error: Optional[str] = None,
        finished: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.available:
            return
        try:
            with self._session() as conn:
                conn.execute(
                    """
                    INSERT INTO etl.backup_log
                        (kind, target, status, size_bytes, object_count, storage_class,
                         error, host, finished_at, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        kind,
                        target,
                        status,
                        size_bytes,
                        object_count,
                        storage_class,
                        error,
                        self.host,
                        _utcnow() if finished else None,
                        self._wrap_json(metadata or {}),
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("record_backup(%s) failed: %s", kind, exc)
