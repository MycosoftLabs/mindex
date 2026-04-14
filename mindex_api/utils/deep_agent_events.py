"""
Best-effort Deep Agent domain event dispatch to MAS.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    raw = os.getenv("MYCA_DEEP_AGENTS_DOMAIN_HOOKS_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _mas_base_url() -> str:
    return (os.getenv("MAS_API_URL") or "http://192.168.0.188:8001").rstrip("/")


async def submit_domain_event(
    *,
    domain: str,
    task: str,
    context: Optional[Dict[str, Any]] = None,
    preferred_agent: Optional[str] = None,
) -> None:
    if not _enabled():
        return

    payload = {
        "domain": domain,
        "task": task,
        "context": context or {},
        "preferred_agent": preferred_agent,
    }
    url = f"{_mas_base_url()}/api/deep-agents/domain-event"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(url, json=payload)
    except Exception:
        logger.debug("Deep Agent domain event dispatch failed for %s", domain, exc_info=True)


def schedule_domain_event(
    *,
    domain: str,
    task: str,
    context: Optional[Dict[str, Any]] = None,
    preferred_agent: Optional[str] = None,
) -> None:
    try:
        asyncio.create_task(
            submit_domain_event(
                domain=domain,
                task=task,
                context=context,
                preferred_agent=preferred_agent,
            )
        )
    except RuntimeError:
        logger.debug("No running event loop for Deep Agent event (%s)", domain)
