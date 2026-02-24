"""
MINDEX A2A-Compatible Agent Surface - February 17, 2026

Read-only agent interface for search and stats intents.
MAS can delegate MINDEX queries here instead of direct coupling.

A2A Protocol compatible - returns Agent Card and accepts message/send
for search and stats intents.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/a2a", tags=["a2a-agent"])

MINDEX_A2A_ENABLED = os.getenv("MINDEX_A2A_ENABLED", "true").lower() in ("1", "true", "yes")


class PartModel(BaseModel):
    text: Optional[str] = None
    url: Optional[str] = None
    data: Optional[Any] = None
    mediaType: Optional[str] = None


class MessageModel(BaseModel):
    messageId: str
    contextId: Optional[str] = None
    taskId: Optional[str] = None
    role: str
    parts: List[PartModel] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None


class SendMessageRequest(BaseModel):
    message: MessageModel
    configuration: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


def _extract_text(msg: MessageModel) -> str:
    if not msg.parts:
        return ""
    return " ".join(p.text or "" for p in msg.parts).strip()


@router.get("/.well-known/agent-card.json")
async def get_agent_card(request: Request) -> JSONResponse:
    """A2A Agent Card for MINDEX read-only agent."""
    if not MINDEX_A2A_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "A2A not enabled"})
    base = str(request.base_url).rstrip("/")
    # Interface URL: same origin + /api/mindex/a2a/v1 (or derived from request path)
    from ..config import settings
    prefix = settings.api_prefix
    interface_url = f"{base}{prefix}/a2a/v1"
    card = {
        "name": "MINDEX Agent",
        "description": "Read-only agent for MINDEX unified search and statistics. Queries species, compounds, genetics, observations, and research data.",
        "version": "1.0.0",
        "provider": {"url": "https://mycosoft.com", "organization": "Mycosoft"},
        "supportedInterfaces": [
            {"url": interface_url, "protocolBinding": "HTTP+JSON", "protocolVersion": "0.3"},
        ],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [
            {
                "id": "search",
                "name": "Unified Search",
                "description": "Search across taxa, compounds, genetics, observations, and research.",
                "tags": ["search", "species", "compounds", "genetics", "observations"],
            },
            {
                "id": "stats",
                "name": "Statistics",
                "description": "Get MINDEX database statistics and counts.",
                "tags": ["stats", "counts", "database"],
            },
        ],
    }
    return JSONResponse(content=card)


@router.post("/v1/message/send")
async def send_message(
    request: Request,
    body: SendMessageRequest,
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """
    A2A message/send - routes search/stats intents to MINDEX.
    Returns task with artifact containing search results or stats.
    """
    from uuid import uuid4
    from datetime import datetime, timezone

    if not MINDEX_A2A_ENABLED:
        return JSONResponse(status_code=404, content={"detail": "A2A not enabled"})

    user_text = _extract_text(body.message).lower()
    context_id = body.message.contextId or str(uuid4())
    task_id = str(uuid4())

    # Detect intent: search vs stats
    is_stats = any(
        w in user_text
        for w in ("count", "stat", "how many", "total", "number of", "stats")
    )

    result_text: str
    if is_stats:
        try:
            from sqlalchemy import text
            # Use core schema tables - MINDEX uses core.taxon, core.compounds, core.dna_sequences
            rows = await session.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM core.taxon) as taxon_count,
                        (SELECT COUNT(*) FROM core.compounds) as compound_count,
                        (SELECT COUNT(*) FROM core.dna_sequences) as genetics_count
                    """
                )
            )
            r = rows.fetchone()
            if r:
                result_text = (
                    f"MINDEX statistics: {r[0] or 0} taxa, "
                    f"{r[1] or 0} compounds, {r[2] or 0} genetics sequences."
                )
            else:
                result_text = "MINDEX statistics: data not available."
        except Exception as e:
            logger.warning("MINDEX stats failed: %s", e)
            result_text = f"Could not retrieve MINDEX statistics: {str(e)}"
    else:
        # Search intent - call unified search helpers
        query = user_text
        if not query or len(query) < 2:
            query = "fungi"
        try:
            from . import unified_search
            import asyncio
            tasks = [
                unified_search.search_taxa(session, query, 5),
                unified_search.search_compounds(session, query, 5),
                unified_search.search_genetics(session, query, 5),
            ]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)
            results = {
                "taxa": results_list[0] if not isinstance(results_list[0], Exception) else [],
                "compounds": results_list[1] if not isinstance(results_list[1], Exception) else [],
                "genetics": results_list[2] if not isinstance(results_list[2], Exception) else [],
            }
            total = sum(len(v) for v in results.values())
            if total > 0:
                lines = [f"Found {total} results for '{query}':"]
                for kind, items in results.items():
                    if items:
                        lines.append(f"  {kind}: {len(items)}")
                        for it in items[:3]:
                            name = (
                                getattr(it, "scientific_name", None)
                                or getattr(it, "name", None)
                                or getattr(it, "species_name", None)
                                or str(getattr(it, "id", ""))
                            )
                            lines.append(f"    - {name}")
                result_text = "\n".join(lines)
            else:
                result_text = f"No results found for '{query}'."
        except Exception as e:
            logger.warning("MINDEX search failed: %s", e)
            result_text = f"Search failed: {str(e)}"

    now = datetime.now(timezone.utc).isoformat()
    task = {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": "TASK_STATE_COMPLETED", "timestamp": now},
        "artifacts": [
            {
                "artifactId": str(uuid4()),
                "name": "result",
                "parts": [{"text": result_text, "mediaType": "text/plain"}],
            }
        ],
        "history": [],
        "metadata": {"protocol": "a2a", "source": "mindex-agent"},
    }
    return JSONResponse(content=task)
