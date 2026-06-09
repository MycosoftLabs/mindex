"""
SourceAgent — a single supervised ETL sub-agent.
=================================================
Wraps one ingestion callable (a `mindex_etl.jobs.*` entrypoint, an earth-data
sync function, or a system maintenance task) and gives it autonomy:

- its own schedule (``schedule_seconds``)
- its own watermark / cursor (opaque JSON, owned by the job)
- health tracking with exponential backoff on failure
- rate-limit (HTTP 429) and downtime (HTTP 503) aware cooldowns

The class is deliberately dependency-free (stdlib only) and side-effect-free:
it never touches the database or network itself. The orchestrator runs the
callable and persists state via ``AgentStateStore``. That separation keeps the
scheduling logic fully unit-testable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"
    FAILED = "failed"


# Result classification used to pick a backoff policy.
STATUS_SUCCESS = "success"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_DOWNTIME = "downtime"
STATUS_ERROR = "error"


@dataclass
class RunResult:
    """Outcome of a single agent invocation."""

    agent_name: str
    status: str = STATUS_SUCCESS
    records: int = 0
    error: Optional[str] = None
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUCCESS


def classify_exception(exc: BaseException) -> str:
    """Map an exception to a backoff class using the same heuristics the
    legacy aggressive_runner used (string sniffing), but centralized."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if "servicedowntime" in text or "503" in text or "downtime" in text:
        return STATUS_DOWNTIME
    if (
        "rate" in text
        or "429" in text
        or "too many" in text
        or "quota" in text
        or "throttl" in text
    ):
        return STATUS_RATE_LIMITED
    return STATUS_ERROR


def normalize_record_count(raw: Any) -> int:
    """Jobs historically return either an int or a stats dict. Normalize."""
    if isinstance(raw, bool):  # guard: bool is an int subclass
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, dict):
        for key in (
            "total_processed",
            "total_records",
            "records",
            "total",
            "inserted",
            "synced",
            "count",
            "images_downloaded",
            "total_publications",
            "total_compounds",
        ):
            val = raw.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return int(val)
    return 0


@dataclass
class SourceAgent:
    """A schedulable, self-healing ETL sub-agent."""

    name: str
    run_func: Callable[..., Any]
    source: str = ""
    kind: str = "source"  # "source" | "system"
    description: str = ""
    priority: int = 100
    schedule_seconds: int = 86_400
    concurrency_group: str = "default"
    enabled: bool = True
    max_pages: Optional[int] = None
    domain_mode: Optional[str] = None
    extra_kwargs: Dict[str, Any] = field(default_factory=dict)

    # Backoff policy (seconds).
    error_backoff_base: int = 120
    error_backoff_max: int = 6 * 3600
    rate_limit_cooldown: int = 300
    downtime_cooldown: int = 6 * 3600

    # ---- live state (persisted) -------------------------------------------
    status: AgentStatus = AgentStatus.IDLE
    last_run_at: Optional[datetime] = None
    last_finished_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    last_status: Optional[str] = None
    last_records: int = 0
    last_duration_ms: int = 0
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    total_runs: int = 0
    total_records: int = 0
    watermark: Dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Scheduling
    # -----------------------------------------------------------------------
    def is_due(self, now: Optional[datetime] = None) -> bool:
        """True when this agent should run right now."""
        if not self.enabled:
            return False
        if self.status == AgentStatus.RUNNING:
            return False
        now = now or _utcnow()
        if self.cooldown_until and now < self.cooldown_until:
            return False
        if self.next_run_at is None:
            return True
        return now >= self.next_run_at

    def seconds_until_due(self, now: Optional[datetime] = None) -> float:
        now = now or _utcnow()
        target = self.next_run_at or now
        if self.cooldown_until and self.cooldown_until > target:
            target = self.cooldown_until
        return max(0.0, (target - now).total_seconds())

    def build_kwargs(self) -> Dict[str, Any]:
        """Assemble keyword arguments for the wrapped callable."""
        kwargs: Dict[str, Any] = dict(self.extra_kwargs)
        if self.max_pages is not None and "max_pages" not in kwargs:
            kwargs["max_pages"] = self.max_pages
        if self.domain_mode and "domain_mode" not in kwargs:
            kwargs["domain_mode"] = self.domain_mode
        return kwargs

    # -----------------------------------------------------------------------
    # Execution (pure: returns a RunResult, updates in-memory state only)
    # -----------------------------------------------------------------------
    def invoke(self) -> RunResult:
        """Run the wrapped callable once and update local health state.

        Never raises — failures are captured into the RunResult so the
        orchestrator loop can keep going. Backoff/next-run scheduling is
        applied here so behavior is identical whether run by the orchestrator
        or directly in a test.
        """
        started = _utcnow()
        self.status = AgentStatus.RUNNING
        self.last_run_at = started
        t0 = time.monotonic()
        result = RunResult(agent_name=self.name, started_at=started)

        try:
            raw = self.run_func(**self.build_kwargs())
            records = normalize_record_count(raw)
            result.records = records
            result.status = STATUS_SUCCESS
        except BaseException as exc:  # noqa: BLE001 — agents must never crash the loop
            result.status = classify_exception(exc)
            result.error = f"{type(exc).__name__}: {exc}"[:1000]

        result.finished_at = _utcnow()
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        self._apply_result(result)
        return result

    # -----------------------------------------------------------------------
    # State transitions
    # -----------------------------------------------------------------------
    def _apply_result(self, result: RunResult) -> None:
        now = result.finished_at or _utcnow()
        self.last_finished_at = now
        self.last_status = result.status
        self.last_duration_ms = result.duration_ms
        self.total_runs += 1

        if result.ok:
            self.consecutive_failures = 0
            self.last_records = result.records
            self.total_records += max(0, result.records)
            self.last_error = None
            self.cooldown_until = None
            self.status = AgentStatus.IDLE
            self.next_run_at = self._plus(now, self.schedule_seconds)
            return

        # Failure path -------------------------------------------------------
        self.consecutive_failures += 1
        self.last_error = result.error
        self.last_records = 0
        cooldown = self._cooldown_for(result.status)
        self.cooldown_until = self._plus(now, cooldown)
        # Next regular run is at least one schedule away, but never before the
        # cooldown expires.
        self.next_run_at = max(
            self._plus(now, self.schedule_seconds),
            self.cooldown_until,
        )
        self.status = AgentStatus.FAILED if result.status == STATUS_ERROR else AgentStatus.COOLDOWN

    def _cooldown_for(self, status: str) -> int:
        if status == STATUS_DOWNTIME:
            return self.downtime_cooldown
        if status == STATUS_RATE_LIMITED:
            # Rate-limit backoff grows a little but stays bounded.
            return min(
                self.rate_limit_cooldown * max(1, self.consecutive_failures),
                self.error_backoff_max,
            )
        # Generic error: exponential backoff.
        exp = self.error_backoff_base * (2 ** (self.consecutive_failures - 1))
        return min(int(exp), self.error_backoff_max)

    @staticmethod
    def _plus(moment: datetime, seconds: float) -> datetime:
        return datetime.fromtimestamp(moment.timestamp() + seconds, tz=timezone.utc)

    # -----------------------------------------------------------------------
    # Serialization helpers (used by the state store + API)
    # -----------------------------------------------------------------------
    def to_state_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "kind": self.kind,
            "concurrency_group": self.concurrency_group,
            "description": self.description,
            "enabled": self.enabled,
            "priority": self.priority,
            "schedule_seconds": self.schedule_seconds,
            "max_pages": self.max_pages,
            "domain_mode": self.domain_mode,
            "status": self.status.value if isinstance(self.status, AgentStatus) else str(self.status),
            "last_run_at": self.last_run_at,
            "last_finished_at": self.last_finished_at,
            "next_run_at": self.next_run_at,
            "cooldown_until": self.cooldown_until,
            "last_status": self.last_status,
            "last_records": self.last_records,
            "last_duration_ms": self.last_duration_ms,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "total_runs": self.total_runs,
            "total_records": self.total_records,
            "watermark": self.watermark,
        }

    def load_state_dict(self, row: Dict[str, Any]) -> None:
        """Restore persisted live state after a restart (registry fields like
        schedule/priority stay authoritative from code unless overridden in
        the DB by an operator pause/resume)."""
        if row.get("enabled") is not None:
            self.enabled = bool(row["enabled"])
        self.last_run_at = row.get("last_run_at")
        self.last_finished_at = row.get("last_finished_at")
        self.next_run_at = row.get("next_run_at")
        self.cooldown_until = row.get("cooldown_until")
        self.last_status = row.get("last_status")
        self.last_records = int(row.get("last_records") or 0)
        self.last_duration_ms = int(row.get("last_duration_ms") or 0)
        self.last_error = row.get("last_error")
        self.consecutive_failures = int(row.get("consecutive_failures") or 0)
        self.total_runs = int(row.get("total_runs") or 0)
        self.total_records = int(row.get("total_records") or 0)
        wm = row.get("watermark")
        if isinstance(wm, dict):
            self.watermark = wm
        # Restored agents that were mid-run when the process died should be
        # treated as idle (the run is gone), not stuck "running".
        if not self.enabled:
            self.status = AgentStatus.DISABLED
        else:
            self.status = AgentStatus.IDLE
