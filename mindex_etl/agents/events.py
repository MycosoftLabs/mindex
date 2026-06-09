"""
EventBus — livestream plumbing for the agent runtime.
=====================================================
Every agent start/finish, heartbeat, and backup is published as a small JSON
event. Events go to:

1. an in-process ring buffer (cheap introspection, tests), and
2. a Redis pub/sub channel + capped stream (``mindex:etl:events``) when Redis
   is configured, so the API's SSE livestream and any external consumer get
   low-latency updates without polling.

The orchestrator runs in its own process/container, so Redis (not in-process
queues) is the cross-process bridge to the API. If Redis is absent the API
falls back to polling the ``etl.*`` tables — the livestream still works, just
at the poll cadence.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger("mindex.agents.events")

EVENTS_CHANNEL = "mindex:etl:events"
EVENTS_STREAM = "mindex:etl:stream"
_STREAM_MAXLEN = 5000


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventBus:
    """Best-effort pub/sub for orchestrator events."""

    def __init__(self, buffer_size: int = 500, redis_url: Optional[str] = None) -> None:
        self.buffer: Deque[Dict[str, Any]] = deque(maxlen=buffer_size)
        self._redis = None
        self._redis_url = redis_url or os.getenv("REDIS_URL")
        self._init_redis()

    def _init_redis(self) -> None:
        if not self._redis_url:
            return
        try:
            import redis  # type: ignore

            self._redis = redis.Redis.from_url(
                self._redis_url, socket_timeout=2, socket_connect_timeout=2
            )
            self._redis.ping()
            logger.info("EventBus connected to Redis (%s)", EVENTS_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            self._redis = None
            logger.info("EventBus running without Redis (%s); API will poll DB.", exc)

    def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        event = {"type": event_type, "ts": _utcnow_iso(), **payload}
        self.buffer.append(event)
        if self._redis is None:
            return
        try:
            data = json.dumps(event, default=str)
            self._redis.publish(EVENTS_CHANNEL, data)
            # Capped stream gives late subscribers recent history.
            self._redis.xadd(
                EVENTS_STREAM, {"event": data}, maxlen=_STREAM_MAXLEN, approximate=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventBus redis publish failed: %s", exc)

    # Convenience emitters -------------------------------------------------
    def agent_started(self, agent, cycle: int) -> None:
        self.publish(
            "agent_started",
            {"agent": agent.name, "source": agent.source, "kind": agent.kind, "cycle": cycle},
        )

    def agent_finished(self, agent, result, cycle: int) -> None:
        self.publish(
            "agent_finished",
            {
                "agent": agent.name,
                "source": agent.source,
                "kind": agent.kind,
                "status": result.status,
                "records": result.records,
                "duration_ms": result.duration_ms,
                "error": result.error,
                "next_run_at": agent.next_run_at.isoformat() if agent.next_run_at else None,
                "cycle": cycle,
            },
        )

    def heartbeat(self, snapshot: Dict[str, Any]) -> None:
        self.publish("heartbeat", snapshot)

    def backup(self, payload: Dict[str, Any]) -> None:
        self.publish("backup", payload)

    def recent(self, limit: int = 100):
        items = list(self.buffer)
        return items[-limit:]
