"""
MindexOrchestrator — the supervising "MINDEX agent".
====================================================
Owns the lifecycle of every sub-agent: decides what is due, runs due agents in a
bounded thread pool while honoring per-host concurrency groups, records each run
to ``etl.*``, emits livestream events + heartbeats, and applies operator controls
(pause/resume/run-now) between ticks.

This single runtime replaces the two competing legacy loops
(``mindex_etl.scheduler`` and ``mindex_etl.aggressive_runner``) — both remain
importable for backward compatibility, but the orchestrator is the thing you
run to keep MINDEX "always on".

Run it::

    python -m mindex_etl.orchestrator            # supervised forever
    python -m mindex_etl.orchestrator --once     # one tick (cron/n8n)
    python -m mindex_etl.orchestrator --list      # list agents and exit
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .base import AgentStatus, SourceAgent
from .events import EventBus
from .registry import build_agents
from .state import AgentStateStore

logger = logging.getLogger("mindex.agents.orchestrator")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MindexOrchestrator:
    """Supervises all MINDEX ETL sub-agents."""

    def __init__(
        self,
        agents: Optional[Dict[str, SourceAgent]] = None,
        *,
        state_store: Optional[AgentStateStore] = None,
        event_bus: Optional[EventBus] = None,
        max_concurrency: Optional[int] = None,
        heartbeat_seconds: int = 15,
        include_system: bool = True,
    ) -> None:
        self.agents: Dict[str, SourceAgent] = agents or {}
        self.state = state_store or AgentStateStore()
        self.events = event_bus or EventBus()
        self.max_concurrency = max_concurrency or int(os.getenv("MINDEX_AGENT_CONCURRENCY", "4"))
        self.heartbeat_seconds = heartbeat_seconds
        self.include_system = include_system
        # How often to re-read operator controls (pause/resume/run-now) from the DB.
        self.control_refresh_seconds = int(os.getenv("MINDEX_AGENT_CONTROL_REFRESH", "8"))

        self.running = True
        self.started_at = _utcnow()
        self.cycle = 0
        self._executor: Optional[ThreadPoolExecutor] = None
        self._inflight: Dict[str, Future] = {}
        self._run_ids: Dict[str, Optional[int]] = {}
        self._busy_groups: set[str] = set()
        self._last_heartbeat = 0.0
        self._last_control_refresh = 0.0
        self._lock = threading.Lock()
        self.stats = {"runs": 0, "records": 0, "failures": 0}

    # ------------------------------------------------------------------
    def load(self) -> None:
        """Build the agent registry, ensure persistence, resume prior state."""
        if not self.agents:
            self.agents = build_agents(include_system=self.include_system)
        self.state.ensure_schema()
        for agent in self.agents.values():
            self.state.upsert_agent(agent)
            self.state.load_state(agent)
        logger.info(
            "Orchestrator loaded %d agents (max_concurrency=%d, persistence=%s)",
            len(self.agents),
            self.max_concurrency,
            "on" if self.state.available else "off",
        )

    # ------------------------------------------------------------------
    def _maybe_refresh_controls(self, force: bool = False) -> None:
        """Pick up operator pause/resume/run-now from the DB, throttled so we
        don't query per-agent on every tick."""
        now = time.monotonic()
        if not force and (now - self._last_control_refresh) < self.control_refresh_seconds:
            return
        self._last_control_refresh = now
        for agent in self.agents.values():
            if agent.name not in self._inflight:
                self.state.refresh_control_flags(agent)

    def _due_agents(self, now: datetime) -> List[SourceAgent]:
        due: List[SourceAgent] = []
        for agent in self.agents.values():
            if agent.name in self._inflight:
                continue
            if agent.is_due(now):
                due.append(agent)
        due.sort(key=lambda a: a.priority)
        return due

    def _can_launch(self, agent: SourceAgent) -> bool:
        if len(self._inflight) >= self.max_concurrency:
            return False
        if agent.concurrency_group in self._busy_groups:
            return False
        return True

    def _launch(self, agent: SourceAgent) -> None:
        assert self._executor is not None
        agent.status = AgentStatus.RUNNING
        self._busy_groups.add(agent.concurrency_group)
        run_id = self.state.record_run_start(agent, self.cycle)
        self._run_ids[agent.name] = run_id
        self.events.agent_started(agent, self.cycle)
        logger.info("[%s] start (group=%s)", agent.name, agent.concurrency_group)
        self._inflight[agent.name] = self._executor.submit(agent.invoke)

    def _reap(self) -> None:
        """Finalize any completed agent runs."""
        done = [name for name, fut in self._inflight.items() if fut.done()]
        for name in done:
            fut = self._inflight.pop(name)
            agent = self.agents[name]
            self._busy_groups.discard(agent.concurrency_group)
            run_id = self._run_ids.pop(name, None)
            try:
                result = fut.result()
            except BaseException as exc:  # noqa: BLE001 — invoke shouldn't raise, but be safe
                logger.error("[%s] invoke crashed: %s", name, exc)
                continue

            self.state.record_run_finish(run_id, result)
            self.state.save_state(agent)
            self.events.agent_finished(agent, result, self.cycle)
            self.stats["runs"] += 1
            if result.ok:
                self.stats["records"] += max(0, result.records)
                logger.info(
                    "[%s] done: %s records in %dms (next %s)",
                    name,
                    f"{result.records:,}",
                    result.duration_ms,
                    agent.next_run_at.isoformat() if agent.next_run_at else "?",
                )
            else:
                self.stats["failures"] += 1
                logger.warning(
                    "[%s] %s: %s (cooldown until %s)",
                    name,
                    result.status,
                    (result.error or "")[:160],
                    agent.cooldown_until.isoformat() if agent.cooldown_until else "?",
                )

    # ------------------------------------------------------------------
    def _heartbeat(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_heartbeat) < self.heartbeat_seconds:
            return
        self._last_heartbeat = now
        snapshot = {
            "started_at": self.started_at,
            "cycle": self.cycle,
            "agents_total": len(self.agents),
            "agents_enabled": sum(1 for a in self.agents.values() if a.enabled),
            "agents_running": len(self._inflight),
            "max_concurrency": self.max_concurrency,
            "stats": dict(self.stats),
        }
        self.state.write_heartbeat(snapshot)
        self.events.heartbeat(
            {
                "cycle": self.cycle,
                "running": sorted(self._inflight.keys()),
                "agents_total": len(self.agents),
                "stats": dict(self.stats),
                "persistence": self.state.available,
            }
        )

    # ------------------------------------------------------------------
    def run_once(self) -> Dict[str, int]:
        """Launch all currently-due agents, wait for them, finalize. Returns a
        per-agent record count. Intended for cron/n8n style single passes."""
        self.load()
        self.cycle += 1
        results: Dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            self._executor = executor
            self._maybe_refresh_controls(force=True)
            now = _utcnow()
            # Launch in waves so concurrency groups are respected.
            pending = self._due_agents(now)
            while pending or self._inflight:
                for agent in list(pending):
                    if self._can_launch(agent):
                        self._launch(agent)
                        pending.remove(agent)
                self._reap_blocking_step(results)
                pending = [a for a in pending if a.name not in self._inflight]
        self._executor = None
        self._heartbeat(force=True)
        logger.info("run_once complete: %s", results)
        return results

    def _reap_blocking_step(self, results: Dict[str, int]) -> None:
        """Wait briefly for at least one in-flight agent, then finalize."""
        if not self._inflight:
            return
        # Wait for the soonest-finishing future to avoid a busy spin.
        for name, fut in list(self._inflight.items()):
            try:
                fut.result(timeout=0.2)
            except Exception:
                continue
            break
        before = set(self._inflight)
        self._reap()
        for name in before - set(self._inflight):
            agent = self.agents.get(name)
            if agent is not None:
                results[name] = agent.last_records if agent.last_status == "success" else -1

    # ------------------------------------------------------------------
    def run_forever(self, poll_seconds: float = 2.0) -> None:
        """Supervised continuous loop. This is the MINDEX 'always on' runtime."""
        self._install_signal_handlers()
        if not self._acquire_lock():
            logger.error("Another MINDEX orchestrator holds the lock; exiting.")
            return

        self.load()
        logger.info("=" * 70)
        logger.info("MINDEX ORCHESTRATOR ONLINE — supervising %d agents", len(self.agents))
        logger.info("=" * 70)
        self._executor = ThreadPoolExecutor(max_workers=self.max_concurrency)
        try:
            while self.running:
                self.cycle += 1
                self._reap()
                self._maybe_refresh_controls()
                now = _utcnow()
                for agent in self._due_agents(now):
                    if not self.running:
                        break
                    if self._can_launch(agent):
                        self._launch(agent)
                self._heartbeat()
                # Sleep, but stay responsive to shutdown + finishing agents.
                slept = 0.0
                while self.running and slept < poll_seconds:
                    if any(f.done() for f in self._inflight.values()):
                        break
                    time.sleep(0.25)
                    slept += 0.25
        finally:
            self._shutdown()

    # ------------------------------------------------------------------
    def _shutdown(self) -> None:
        logger.info("Orchestrator draining %d in-flight agents...", len(self._inflight))
        if self._executor is not None:
            self._executor.shutdown(wait=True)
        self._reap()
        for agent in self.agents.values():
            if agent.status == AgentStatus.RUNNING:
                agent.status = AgentStatus.IDLE
                self.state.save_state(agent)
        self._heartbeat(force=True)
        self._release_lock()
        logger.info("Orchestrator stopped. Lifetime stats: %s", self.stats)

    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):  # noqa: ANN001
            logger.info("Signal %s received — shutting down after current agents.", signum)
            self.running = False

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except ValueError:
            # Not on the main thread (e.g. embedded) — skip signal install.
            pass

    # ------------------------------------------------------------------
    _lock_file = None

    def _acquire_lock(self) -> bool:
        """Single-instance advisory lock (matches aggressive_runner behavior)."""
        try:
            import fcntl  # type: ignore

            self._lock_file = open("/tmp/mindex_orchestrator.lock", "w", encoding="utf-8")
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False
        except Exception:
            # Non-Linux / restricted env — don't block startup.
            return True

    def _release_lock(self) -> None:
        try:
            if self._lock_file is not None:
                self._lock_file.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def describe(self) -> List[Dict]:
        """Lightweight registry dump for --list / API fallback."""
        return [
            {
                "name": a.name,
                "source": a.source,
                "kind": a.kind,
                "group": a.concurrency_group,
                "priority": a.priority,
                "schedule_seconds": a.schedule_seconds,
                "enabled": a.enabled,
                "description": a.description,
            }
            for a in sorted(self.agents.values(), key=lambda x: x.priority)
        ]
