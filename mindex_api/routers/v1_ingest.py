"""
Versioned public ingest surface under /api/v1/ingest/*.

- MYCA verified entities → crep.unified_entities (defense/infra waypoints)
- iNaturalist region warm-cache → crep.project_nature_cache

All routes require internal service auth (X-Internal-Token or legacy X-API-Key).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_internal_token
from ..config import settings
from ..dependencies import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ingest",
    tags=["v1-ingest"],
    dependencies=[Depends(require_internal_token)],
)

# Project regions: logical slug → (swlat, swlng, nelat, nelng) for iNaturalist bbox API
_INAT_REGION_BBOX: Dict[str, Tuple[float, float, float, float]] = {
    "oyster": (37.2, -76.0, 37.5, -75.5),
    "goffs": (35.0, -115.2, 35.1, -115.0),
    "mojave": (35.0, -116.0, 35.3, -115.5),
}


def _parse_ts(raw: Any) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip()
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid collected_at timestamp",
        ) from exc


def _stable_myca_id(body: dict) -> str:
    vf = body.get("verified_from") or {}
    wpid = vf.get("waypoint_id")
    if wpid is not None and str(wpid).strip() != "":
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(wpid))[:200]
        return f"myca_vw_{safe}"
    # Fallback: not ideal for deduplication, but unblocks ingest
    lat = body.get("lat")
    lng = body.get("lng")
    h = hashlib.sha256(
        f"{lat}:{lng}:{body.get('name')!s}".encode("utf-8", errors="replace")
    ).hexdigest()[:16]
    return f"myca_vw_adhoc_{h}"


@router.post("/myca-verified-entity")
async def ingest_myca_verified_entity(
    body: dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Persist a MYCA waypoint verification result to crep.unified_entities.
    Replaces any prior row with the same (id) for this source via delete + insert
    to avoid duplicate composite primary keys.
    """
    try:
        lat = float(body["lat"])
        lng = float(body["lng"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="lat and lng (numbers) are required",
        ) from exc

    entity_type = str(body.get("entity_type") or "myca_verified")
    name = (body.get("name") or body.get("display_name") or "MYCA verified")[:500]
    source = str(body.get("source") or "myca-waypoint-verify")[:200]
    confidence = body.get("confidence")
    try:
        conf = float(confidence) if confidence is not None else 0.9
    except (TypeError, ValueError):
        conf = 0.9
    conf = max(0.0, min(1.0, conf))

    observed_at = _parse_ts(body.get("collected_at"))
    valid_from = observed_at
    stable_id = _stable_myca_id(body)

    state: Dict[str, Any] = {
        "name": name,
        "entity_subtype": body.get("entity_subtype"),
        "perimeter": body.get("perimeter"),
        "confidence": conf,
        "verified_from": body.get("verified_from"),
        "citations": body.get("citations"),
    }
    properties: Dict[str, Any] = {
        "layer": "myca_verified",
        "source": source,
        "name": name,
    }

    state_json = json.dumps({k: v for k, v in state.items() if v is not None})
    props_json = json.dumps(properties)

    await db.execute(
        text(
            """
            DELETE FROM crep.unified_entities
            WHERE id = :eid AND source = :src
            """
        ),
        {"eid": stable_id, "src": source},
    )

    ins = text(
        """
        INSERT INTO crep.unified_entities
          (id, entity_type, geometry, state, observed_at, valid_from, valid_to,
           confidence, source, properties, s2_cell_id)
        VALUES
          (:eid, :etype,
           ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
           CAST(:state AS jsonb), :observed_at, :valid_from, NULL,
           :confidence, :source, CAST(:props AS jsonb), 0)
        """
    )
    await db.execute(
        ins,
        {
            "eid": stable_id,
            "etype": entity_type,
            "lng": lng,
            "lat": lat,
            "state": state_json,
            "observed_at": observed_at,
            "valid_from": valid_from,
            "confidence": conf,
            "source": source,
            "props": props_json,
        },
    )
    await db.commit()
    return {"ok": True, "id": stable_id, "entity_type": entity_type, "source": source}


class InatRegionIngestResult(BaseModel):
    ok: bool = True
    region: str
    cache_key: str
    observation_count: int = Field(0, description="Number of iNat observation records returned in this page")
    updated_at: Optional[str] = None


@router.post("/inat-region/{region}", response_model=InatRegionIngestResult)
async def ingest_inat_region(
    region: str = Path(..., description="Logical region slug, or pass bbox query params to override"),
    swlat: Optional[float] = None,
    swlng: Optional[float] = None,
    nelat: Optional[float] = None,
    nelng: Optional[float] = None,
    per_page: int = 200,
    db: AsyncSession = Depends(get_db_session),
) -> InatRegionIngestResult:
    """
    Fetch iNaturalist observations for a bounding box, store the JSON in
    crep.project_nature_cache for CREP/Fluid Search to read without hammering
    the public iNat API.
    """
    rslug = (region or "").strip().lower()
    if rslug not in _INAT_REGION_BBOX and not all(
        x is not None for x in (swlat, swlng, nelat, nelng)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown region {region!r}; provide swlat, swlng, nelat, nelng as query parameters, "
                f"or use one of: {', '.join(sorted(_INAT_REGION_BBOX))}"
            ),
        )
    if all(x is not None for x in (swlat, swlng, nelat, nelng)):
        bbox = (swlat, swlng, nelat, nelng)
    else:
        bbox = _INAT_REGION_BBOX[rslug]

    # Optional token — higher rate limits; never required for read-only.
    inat_base = (settings.inat_api_base or "https://api.inaturalist.org/v1").rstrip("/")
    token = (settings.inat_api_token or "").strip()
    url = f"{inat_base}/observations"
    params: Dict[str, Any] = {
        "swlat": bbox[0],
        "swlng": bbox[1],
        "nelat": bbox[2],
        "nelng": bbox[3],
        "per_page": min(max(1, per_page), 200),
        "order": "desc",
        "order_by": "created_at",
    }
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.get(url, params=params, headers=headers)
    if res.status_code >= 400:
        logger.warning("iNaturalist HTTP %s: %s", res.status_code, res.text[:500])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="iNaturalist API request failed",
        )
    data = res.json()
    results = data.get("results")
    n = len(results) if isinstance(results, list) else 0

    cache_key = f"inat_{bbox[0]:.4f}_{bbox[1]:.4f}_{bbox[2]:.4f}_{bbox[3]:.4f}"
    updated = datetime.now(timezone.utc)
    up = text(
        """
        INSERT INTO crep.project_nature_cache
          (project_key, cache_key, payload, updated_at, expires_at, source_label)
        VALUES
          (:pk, :ck, CAST(:payload AS jsonb), :updated_at, :expires, :src)
        ON CONFLICT (project_key, cache_key) DO UPDATE SET
          payload = EXCLUDED.payload,
          updated_at = EXCLUDED.updated_at,
          expires_at = EXCLUDED.expires_at,
          source_label = EXCLUDED.source_label
        """
    )
    payload_str = json.dumps(data)
    await db.execute(
        up,
        {
            "pk": rslug,
            "ck": cache_key,
            "payload": payload_str,
            "updated_at": updated,
            "expires": None,
            "src": "mindex_ingest_inat",
        },
    )
    await db.commit()
    return InatRegionIngestResult(
        region=rslug,
        cache_key=cache_key,
        observation_count=n,
        updated_at=updated.isoformat(),
    )
