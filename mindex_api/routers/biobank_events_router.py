"""
Biobank event receiver — BLOCKS/MYCODAO tissue catalog → MINDEX.

POST /api/biobank/events ingests species/accession lifecycle events so MINDEX
can link taxonomy, trigger ETL, and expose biobank status to search/MYCA.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/biobank", tags=["biobank"])

_MAX_EVENTS = 500
_recent: Deque[Dict[str, Any]] = deque(maxlen=_MAX_EVENTS)

BiobankEventType = Literal[
    "species_added",
    "accession_created",
    "accession_updated",
    "tissue_transferred",
    "tissue_contaminated",
    "tissue_observed",
]


class BiobankEventIn(BaseModel):
    source: str = "mycodao-biobank"
    at: Optional[str] = None
    event: BiobankEventType
    accessionCode: Optional[str] = None
    taxonCode: Optional[str] = None
    scientificName: Optional[str] = None
    commonName: Optional[str] = None
    status: Optional[str] = None
    health: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    performedBy: Optional[str] = None


def _verify_token(authorization: Optional[str]) -> None:
    expected = os.environ.get("BIOBANK_WEBHOOK_TOKEN", "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


async def _maybe_link_taxon(evt: BiobankEventIn) -> Optional[Dict[str, Any]]:
    """On species_added, record intent to link MINDEX taxonomy (ETL can pick up)."""
    if evt.event != "species_added" or not evt.scientificName:
        return None
    return {
        "pendingTaxonLink": True,
        "scientificName": evt.scientificName,
        "taxonCode": evt.taxonCode,
    }


@router.post("/events")
async def receive_biobank_event(
    body: BiobankEventIn,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Ingest a biobank lifecycle event from BLOCKS."""
    _verify_token(authorization)
    taxon_link = await _maybe_link_taxon(body)
    record = {
        "id": str(uuid4()),
        "receivedAt": datetime.now(timezone.utc).isoformat(),
        **body.model_dump(exclude_none=True),
    }
    if taxon_link:
        record["mindexLink"] = taxon_link
    _recent.append(record)
    logger.info(
        "biobank event %s accession=%s scientific=%s",
        body.event,
        body.accessionCode,
        body.scientificName,
    )
    return {"ok": True, "id": record["id"], "event": body.event, "mindexLink": taxon_link}


@router.get("/events/recent")
async def list_recent_biobank_events(limit: int = 50) -> Dict[str, Any]:
    cap = max(1, min(limit, _MAX_EVENTS))
    items: List[Dict[str, Any]] = list(_recent)[-cap:]
    return {"count": len(items), "items": list(reversed(items))}


@router.get("/health")
async def biobank_receiver_health() -> Dict[str, str]:
    return {"status": "healthy", "receiver": "mindex"}
