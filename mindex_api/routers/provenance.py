"""Tenant-scoped local provenance lifecycle; no live signing or broadcast routes."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db_session
from ..ledger import provenance as engine
from ..provenance_access import (
    authorized_artifact_hashes,
    require_ledger_principal,
    require_operator,
    trusted_source_key,
)

class PrivateLedgerRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                if request.method == "POST":
                    if request.headers.get("content-encoding", "identity") != "identity":
                        raise HTTPException(415, "unsupported_content_encoding")
                    chunks, size = [], 0
                    async with asyncio.timeout(10):
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > 65536:
                                raise HTTPException(413, "provenance_request_too_large")
                            chunks.append(chunk)
                    request._body = b"".join(chunks)
                response = await original(request)
            except HTTPException as exc:
                response = JSONResponse({"error": exc.detail}, status_code=exc.status_code)
            except TimeoutError:
                response = JSONResponse({"error": "provenance_request_timeout"}, status_code=408)
            except RequestValidationError:
                response = JSONResponse({"error": "invalid_provenance_request"}, status_code=422)
            except Exception as exc:
                # Shared retention errors contain safe stable codes; no database,
                # private object coordinates, raw bytes, or token exception text.
                if type(exc).__name__ == "RetentionError" and type(exc).__module__ == "mindex_api.retention.contracts":
                    response = JSONResponse({"error": exc.code}, status_code=exc.status)
                else:
                    response = JSONResponse({"error": "provenance_dependency_unavailable"}, status_code=503)
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Authorization, X-Tenant-Id, X-Project-Id"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Provenance-Contract"] = "mindex-provenance-v1"
            return response
        return handler


router = APIRouter(prefix="/ledger/provenance/v1", tags=["Private provenance"], route_class=PrivateLedgerRoute)


def _principal(value):
    # Membership principal has no role claims. Operators use a separate authority.
    return engine.ProvenancePrincipal(
        issuer=value.issuer, subject=value.subject,
        tenant_id=value.tenant_id, project_id=value.project_id,
    )


async def _call(db, operation):
    try:
        return await operation
    except engine.ProvenanceError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, exc.code) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(503, "provenance_storage_unavailable") from exc


@router.get("/records")
async def records(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000),
                  principal=Depends(require_ledger_principal), db: AsyncSession = Depends(get_db_session)):
    return await _call(db, engine.list_records(db, _principal(principal), limit=limit, offset=offset))


@router.get("/queue")
async def queue(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000),
                principal=Depends(require_ledger_principal), db: AsyncSession = Depends(get_db_session)):
    await require_operator(request, principal, None)
    operator = replace(_principal(principal), roles=frozenset({"ledger_operator"}))
    return await _call(db, engine.list_queue(db, operator, limit=limit, offset=offset))


@router.post("/records", status_code=201)
async def register(body: engine.RegisterRequest, request: Request,
                   principal=Depends(require_ledger_principal), db: AsyncSession = Depends(get_db_session)):
    hashes = await authorized_artifact_hashes(request, principal, body.evidence.artifact_ids)
    supplied = {str(key): value for key, value in body.evidence.artifact_digests.items()}
    if hashes != supplied:
        raise HTTPException(409, "retained_artifact_hash_mismatch")
    return await _call(db, engine.register(db, _principal(principal), body))


@router.get("/records/{record_id}")
async def detail(record_id: UUID, principal=Depends(require_ledger_principal),
                 db: AsyncSession = Depends(get_db_session)):
    return await _call(db, engine.get_record(db, _principal(principal), str(record_id)))


@router.get("/records/{record_id}/events")
async def events(record_id: UUID, limit: int = Query(100, ge=1, le=100),
                 offset: int = Query(0, ge=0, le=10000), principal=Depends(require_ledger_principal),
                 db: AsyncSession = Depends(get_db_session)):
    return await _call(db, engine.list_events(db, _principal(principal), str(record_id), limit=limit, offset=offset))


@router.get("/records/{record_id}/lineage")
async def lineage(record_id: UUID, principal=Depends(require_ledger_principal),
                  db: AsyncSession = Depends(get_db_session)):
    return await _call(db, engine.get_lineage(db, _principal(principal), str(record_id)))


@router.post("/records/{record_id}/validate")
async def validate(record_id: UUID, body: engine.ValidateRequest, request: Request,
                   principal=Depends(require_ledger_principal), db: AsyncSession = Depends(get_db_session)):
    record = await _call(db, engine.get_record(db, _principal(principal), str(record_id)))
    key = await trusted_source_key(request, principal, record["source"]["key_id"])
    hashes = await authorized_artifact_hashes(request, principal, record["evidence"]["artifact_ids"])
    return await _call(db, engine.validate(db, _principal(principal), str(record_id), body,
                                          trusted_key=key, artifact_hashes=hashes))


@router.post("/records/{record_id}/approve")
async def approve(record_id: UUID, body: engine.ApprovalRequest, request: Request,
                  principal=Depends(require_ledger_principal), db: AsyncSession = Depends(get_db_session)):
    await require_operator(request, principal, body.policy_version)
    operator = replace(_principal(principal), roles=frozenset({"ledger_operator"}))
    return await _call(db, engine.approve(db, operator, str(record_id), body))


@router.post("/records/{record_id}/reject")
async def reject(record_id: UUID, body: engine.ActionRequest, request: Request,
                 principal=Depends(require_ledger_principal), db: AsyncSession = Depends(get_db_session)):
    await require_operator(request, principal, None)
    operator = replace(_principal(principal), roles=frozenset({"ledger_operator"}))
    return await _call(db, engine.reject(db, operator, str(record_id), body))


@router.post("/records/{record_id}/fail")
async def fail(record_id: UUID, body: engine.ActionRequest, request: Request,
               principal=Depends(require_ledger_principal), db: AsyncSession = Depends(get_db_session)):
    await require_operator(request, principal, None)
    operator = replace(_principal(principal), roles=frozenset({"ledger_operator"}))
    return await _call(db, engine.fail(db, operator, str(record_id), body))


@router.post("/records/{record_id}/submit")
@router.post("/records/{record_id}/verify")
async def disabled_public_proof(record_id: UUID, principal=Depends(require_ledger_principal)):
    raise HTTPException(503, "public_proof_adapter_not_qualified")
