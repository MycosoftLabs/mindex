"""
MYCODAO data zone (schema `mycodao`) + Mycosoft zone registry (schema `mycosoft`).

MYCA (MAS) uses the same internal token as other MINDEX internal routes and can:
- Read `GET .../meta/myca-data-catalog` — zone boundaries and table names
- Read/write MYCODAO intelligence rows under `.../mycodao/*`
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session

router = APIRouter(tags=["mycodao-zone", "mycosoft-meta"])


MYCA_CATALOG_QUERY = """
SELECT zone_code, display_name, pg_schema, product_line, notes
FROM mycosoft.data_zone_registry
ORDER BY product_line, zone_code
"""


@router.get("/meta/myca-data-catalog")
async def myca_data_catalog(db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """
    Single discovery endpoint for MYCA: MYCODAO vs Mycosoft vs shared_infra zones.
    Does not expose secrets; registry rows are documentation + routing hints.
    """
    rows = (await db.execute(text(MYCA_CATALOG_QUERY))).mappings().all()
    zones = [dict(r) for r in rows]
    mycodao_tables = [
        "polymarket_market_snapshots",
        "wallet_stats",
        "market_scores",
        "signal_events",
        "ingestion_runs",
        "telegram_messages",
        "realms_proposal_mirror",
        "x402_audit_log",
    ]
    return {
        "zones": zones,
        "mycodao_schema": "mycodao",
        "mycodao_tables": [f"mycodao.{t}" for t in mycodao_tables],
        "mycosoft_registry_schema": "mycosoft",
        "note": "Mycosoft domain data remains in existing schemas (core, obs, fusarium, telemetry, ...); "
        "only MYCODAO Pulse intelligence lives in mycodao.*",
    }


# --- Read helpers (MYCODAO tables) ---


@router.get("/mycodao/polymarket-snapshots")
async def list_polymarket_snapshots(
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            SELECT id, market_id, snapshot_at, question, outcomes, volume_usd, liquidity_usd,
                   source_version, content_checksum
            FROM mycodao.polymarket_market_snapshots
            ORDER BY snapshot_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/mycodao/wallet-stats")
async def list_wallet_stats(
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            SELECT id, wallet_pubkey, period_start, period_end, metrics, updated_at
            FROM mycodao.wallet_stats
            ORDER BY updated_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/mycodao/signal-events")
async def list_signal_events(
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            SELECT id, signal_type, payload, severity, observed_at
            FROM mycodao.signal_events
            ORDER BY observed_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/mycodao/ingestion-runs")
async def list_ingestion_runs(
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            SELECT id, source, started_at, finished_at, status, records_upserted,
                   error_message, source_version, payload_checksum
            FROM mycodao.ingestion_runs
            ORDER BY started_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


# --- Write bodies (workers / MAS) ---


class PolymarketSnapshotWrite(BaseModel):
    market_id: str
    question: str | None = None
    outcomes: dict[str, Any] | None = None
    volume_usd: float | None = None
    liquidity_usd: float | None = None
    raw_ref: dict[str, Any] | None = None
    source_version: str | None = None
    content_checksum: str | None = None


@router.post("/mycodao/polymarket-snapshots")
async def insert_polymarket_snapshot(
    body: PolymarketSnapshotWrite,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await db.execute(
        text(
            """
            INSERT INTO mycodao.polymarket_market_snapshots (
                market_id, question, outcomes, volume_usd, liquidity_usd,
                raw_ref, source_version, content_checksum
            ) VALUES (
                :market_id, :question, CAST(:outcomes AS jsonb), :volume_usd, :liquidity_usd,
                CAST(:raw_ref AS jsonb), :source_version, :content_checksum
            )
            """
        ),
        {
            "market_id": body.market_id,
            "question": body.question,
            "outcomes": json.dumps(body.outcomes) if body.outcomes is not None else None,
            "volume_usd": body.volume_usd,
            "liquidity_usd": body.liquidity_usd,
            "raw_ref": json.dumps(body.raw_ref) if body.raw_ref is not None else None,
            "source_version": body.source_version,
            "content_checksum": body.content_checksum,
        },
    )
    await db.commit()
    return {"status": "inserted"}


class IngestionRunStart(BaseModel):
    source: str
    source_version: str | None = None


@router.post("/mycodao/ingestion-runs/start")
async def start_ingestion_run(
    body: IngestionRunStart,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    result = await db.execute(
        text(
            """
            INSERT INTO mycodao.ingestion_runs (source, source_version, status)
            VALUES (:source, :source_version, 'running')
            RETURNING id
            """
        ),
        {"source": body.source, "source_version": body.source_version},
    )
    await db.commit()
    run_id = result.scalar_one()
    return {"status": "running", "id": run_id}


class IngestionRunFinish(BaseModel):
    status: Literal["completed", "failed"]
    records_upserted: int = 0
    error_message: str | None = None
    payload_checksum: str | None = None


@router.post("/mycodao/ingestion-runs/{run_id}/finish")
async def finish_ingestion_run(
    run_id: int,
    body: IngestionRunFinish,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await db.execute(
        text(
            """
            UPDATE mycodao.ingestion_runs
            SET finished_at = NOW(),
                status = :status,
                records_upserted = :records_upserted,
                error_message = :error_message,
                payload_checksum = :payload_checksum
            WHERE id = :run_id
            """
        ),
        {
            "run_id": run_id,
            "status": body.status,
            "records_upserted": body.records_upserted,
            "error_message": body.error_message,
            "payload_checksum": body.payload_checksum,
        },
    )
    await db.commit()
    return {"status": "updated", "id": run_id}


class X402AuditWrite(BaseModel):
    agent_id: str | None = None
    policy_id: str | None = None
    simulate_mode: bool = True
    amount_requested: float | None = None
    currency: str | None = None
    http_resource: str | None = None
    status: str
    detail: dict[str, Any] | None = None


@router.post("/mycodao/x402-audit")
async def append_x402_audit(
    body: X402AuditWrite,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await db.execute(
        text(
            """
            INSERT INTO mycodao.x402_audit_log (
                agent_id, policy_id, simulate_mode, amount_requested, currency,
                http_resource, status, detail
            ) VALUES (
                :agent_id, :policy_id, :simulate_mode, :amount_requested, :currency,
                :http_resource, :status, CAST(:detail AS jsonb)
            )
            """
        ),
        {
            "agent_id": body.agent_id,
            "policy_id": body.policy_id,
            "simulate_mode": body.simulate_mode,
            "amount_requested": body.amount_requested,
            "currency": body.currency,
            "http_resource": body.http_resource,
            "status": body.status,
            "detail": json.dumps(body.detail or {}),
        },
    )
    await db.commit()
    return {"status": "logged"}
