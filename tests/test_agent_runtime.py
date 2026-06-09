"""
Unit tests for the MINDEX agent runtime.

These exercise the scheduling / backoff / concurrency logic with in-memory fakes
— no database, network, or optional deps (boto3/redis/psycopg) required. The
state store degrades to a no-op when Postgres is absent, which is exactly the
path tested here.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from mindex_etl.agents.base import (
    STATUS_DOWNTIME,
    STATUS_ERROR,
    STATUS_RATE_LIMITED,
    AgentStatus,
    SourceAgent,
    classify_exception,
    normalize_record_count,
)


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# normalize_record_count
# ---------------------------------------------------------------------------
def test_normalize_record_count_variants():
    assert normalize_record_count(42) == 42
    assert normalize_record_count(3.0) == 3
    assert normalize_record_count(True) == 0          # bool is not a count
    assert normalize_record_count(None) == 0
    assert normalize_record_count("nope") == 0
    assert normalize_record_count({"inserted": 7}) == 7
    assert normalize_record_count({"total_publications": 12}) == 12
    assert normalize_record_count({"unrelated": 5}) == 0


# ---------------------------------------------------------------------------
# classify_exception
# ---------------------------------------------------------------------------
def test_classify_exception():
    assert classify_exception(RuntimeError("HTTP 429 Too Many Requests")) == STATUS_RATE_LIMITED
    assert classify_exception(RuntimeError("rate limited")) == STATUS_RATE_LIMITED
    assert classify_exception(RuntimeError("503 downtime")) == STATUS_DOWNTIME
    assert classify_exception(ValueError("boom")) == STATUS_ERROR


# ---------------------------------------------------------------------------
# is_due scheduling
# ---------------------------------------------------------------------------
def test_is_due_respects_enabled_schedule_cooldown():
    agent = SourceAgent(name="t", run_func=lambda **_: 1, schedule_seconds=3600)

    # fresh agent (no next_run_at) is due
    assert agent.is_due(_utcnow()) is True

    # disabled agent is never due
    agent.enabled = False
    assert agent.is_due(_utcnow()) is False
    agent.enabled = True

    # scheduled in the future -> not due
    agent.next_run_at = _utcnow() + timedelta(hours=1)
    assert agent.is_due(_utcnow()) is False

    # due time passed -> due
    agent.next_run_at = _utcnow() - timedelta(seconds=1)
    assert agent.is_due(_utcnow()) is True

    # cooldown blocks even when next_run_at passed
    agent.cooldown_until = _utcnow() + timedelta(minutes=5)
    assert agent.is_due(_utcnow()) is False

    # running agents are not due
    agent.cooldown_until = None
    agent.status = AgentStatus.RUNNING
    assert agent.is_due(_utcnow()) is False


# ---------------------------------------------------------------------------
# invoke success / failure
# ---------------------------------------------------------------------------
def test_invoke_success_schedules_next_run():
    agent = SourceAgent(name="ok", run_func=lambda **_: 25, schedule_seconds=600)
    result = agent.invoke()

    assert result.ok
    assert result.records == 25
    assert agent.status == AgentStatus.IDLE
    assert agent.consecutive_failures == 0
    assert agent.total_records == 25
    assert agent.total_runs == 1
    # next run is ~schedule_seconds in the future
    delta = (agent.next_run_at - _utcnow()).total_seconds()
    assert 590 <= delta <= 600


def test_invoke_failure_applies_exponential_backoff():
    def boom(**_):
        raise ValueError("kaboom")

    agent = SourceAgent(name="bad", run_func=boom, error_backoff_base=100, schedule_seconds=600)

    r1 = agent.invoke()
    assert r1.status == STATUS_ERROR
    assert agent.consecutive_failures == 1
    assert agent.status == AgentStatus.FAILED
    cd1 = (agent.cooldown_until - _utcnow()).total_seconds()
    assert 90 <= cd1 <= 100  # base * 2^0

    r2 = agent.invoke()
    assert r2.status == STATUS_ERROR
    assert agent.consecutive_failures == 2
    cd2 = (agent.cooldown_until - _utcnow()).total_seconds()
    assert 190 <= cd2 <= 200  # base * 2^1


def test_invoke_downtime_uses_long_cooldown():
    def down(**_):
        raise RuntimeError("503 Service Unavailable")

    agent = SourceAgent(name="svc", run_func=down, downtime_cooldown=6 * 3600)
    result = agent.invoke()
    assert result.status == STATUS_DOWNTIME
    cd = (agent.cooldown_until - _utcnow()).total_seconds()
    assert cd > 5 * 3600


def test_invoke_rate_limit_cooldown():
    def limited(**_):
        raise RuntimeError("HTTP 429")

    agent = SourceAgent(name="rl", run_func=limited, rate_limit_cooldown=300)
    result = agent.invoke()
    assert result.status == STATUS_RATE_LIMITED
    cd = (agent.cooldown_until - _utcnow()).total_seconds()
    assert 290 <= cd <= 320


# ---------------------------------------------------------------------------
# build_kwargs
# ---------------------------------------------------------------------------
def test_build_kwargs_merges_max_pages_and_domain_mode():
    captured = {}

    def job(**kwargs):
        captured.update(kwargs)
        return 0

    agent = SourceAgent(
        name="j", run_func=job, max_pages=10, domain_mode="all",
        extra_kwargs={"per_page": 50},
    )
    agent.invoke()
    assert captured == {"max_pages": 10, "domain_mode": "all", "per_page": 50}


def test_state_round_trip():
    agent = SourceAgent(name="rt", run_func=lambda **_: 5, schedule_seconds=120)
    agent.invoke()
    snap = agent.to_state_dict()

    restored = SourceAgent(name="rt", run_func=lambda **_: 5, schedule_seconds=120)
    restored.load_state_dict(snap)
    assert restored.total_records == agent.total_records
    assert restored.consecutive_failures == agent.consecutive_failures


# ---------------------------------------------------------------------------
# Orchestrator run_once with in-memory state
# ---------------------------------------------------------------------------
def _make_orchestrator(agents):
    from mindex_etl.agents.orchestrator import MindexOrchestrator
    from mindex_etl.agents.state import AgentStateStore

    store = AgentStateStore()
    store.available = False  # force in-memory (no DB in unit tests)
    orch = MindexOrchestrator(
        agents={a.name: a for a in agents},
        state_store=store,
        max_concurrency=4,
        include_system=False,
    )
    return orch


def test_orchestrator_runs_due_agents_once():
    counter = {"a": 0, "b": 0}

    a = SourceAgent(name="a", run_func=lambda **_: counter.__setitem__("a", counter["a"] + 1) or 3)
    b = SourceAgent(name="b", run_func=lambda **_: counter.__setitem__("b", counter["b"] + 1) or 4)
    # b is not due yet
    b.next_run_at = _utcnow() + timedelta(hours=1)

    orch = _make_orchestrator([a, b])
    results = orch.run_once()

    assert counter["a"] == 1
    assert counter["b"] == 0          # not due
    assert results.get("a") == 3
    assert a.total_records == 3


def test_orchestrator_serializes_concurrency_group():
    active = {"n": 0, "max": 0}
    lock = threading.Lock()

    def busy(**_):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.05)
        with lock:
            active["n"] -= 1
        return 1

    # Two agents in the SAME concurrency group must never overlap.
    a = SourceAgent(name="g1", run_func=busy, concurrency_group="shared")
    b = SourceAgent(name="g2", run_func=busy, concurrency_group="shared")

    orch = _make_orchestrator([a, b])
    orch.run_once()

    assert active["max"] == 1, "agents in the same group ran concurrently"


def test_orchestrator_allows_parallel_across_groups():
    active = {"n": 0, "max": 0}
    lock = threading.Lock()

    def busy(**_):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        time.sleep(0.1)
        with lock:
            active["n"] -= 1
        return 1

    a = SourceAgent(name="x", run_func=busy, concurrency_group="ga")
    b = SourceAgent(name="y", run_func=busy, concurrency_group="gb")

    orch = _make_orchestrator([a, b])
    orch.run_once()

    assert active["max"] == 2, "agents in different groups did not run in parallel"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
