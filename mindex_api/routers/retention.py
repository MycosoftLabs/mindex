"""Private retention.v1 routes; original JWT plus server-owned membership only."""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute

from ..retention.contracts import (
    Principal, RetentionConfig, RetentionError, admission_metadata, canonical_uuid, public_receipt,
)
from ..retention.service import RetentionService


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                response = await original(request)
            except RetentionError as exc:
                response = JSONResponse({"contract_version": "retention.v1", "error": exc.code},
                                        status_code=exc.status)
            except Exception:
                response = JSONResponse({"contract_version": "retention.v1", "error": "retention_unavailable"},
                                        status_code=503)
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Authorization, X-Tenant-Id, X-Project-Id"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        return handler


router = APIRouter(prefix="/retention/v1", tags=["private-retention"], route_class=PrivateRoute)


def get_retention_service(request: Request) -> RetentionService:
    existing = getattr(request.app.state, "retention_service", None)
    if existing is not None:
        return existing
    config = RetentionConfig.from_env()
    config.admission()
    # Optional dependencies are loaded only on explicit activation, never at startup.
    from ..db import async_session_scope
    from ..retention.identity import IdentityVerifier
    from ..retention.repository import RetentionRepository
    from ..retention.object_store import create_object_store
    verifier = IdentityVerifier(enabled=True, issuer=os.environ.get("RETENTION_IDENTITY_ISSUER", ""),
        audience=os.environ.get("RETENTION_IDENTITY_AUDIENCE", ""),
        jwks_url=os.environ.get("RETENTION_IDENTITY_JWKS_URL") or None)
    service = RetentionService(RetentionRepository(async_session_scope, config), verifier,
                               create_object_store(config), config)
    request.app.state.retention_service = service
    return service


async def require_principal(request: Request) -> Principal:
    service = get_retention_service(request)
    return await service.authenticate(request.headers.get("authorization", ""),
        request.headers.get("x-tenant-id", ""), request.headers.get("x-project-id", ""))


async def bounded_body(request: Request, limit: int) -> bytes:
    try:
        length = request.headers.get("content-length")
        if length is not None and (int(length) < 0 or int(length) > limit):
            raise RetentionError("payload_size_invalid", 413)
    except ValueError as exc:
        raise RetentionError("invalid_content_length", 422) from exc
    chunks, size = [], 0
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise RetentionError("payload_size_invalid", 413)
                chunks.append(chunk)
    except TimeoutError as exc:
        raise RetentionError("request_timeout", 408) from exc
    if not size:
        raise RetentionError("payload_size_invalid", 413)
    return b"".join(chunks)


@router.get("/principal")
async def principal_route(principal: Principal = Depends(require_principal)):
    return {"contract_version": "retention.v1", **principal.__dict__}


@router.post("/artifacts")
async def admit(request: Request, principal: Principal = Depends(require_principal)):
    service = get_retention_service(request)
    if request.headers.get("content-encoding", "identity") != "identity":
        raise RetentionError("unsupported_content_encoding", 415)
    metadata = admission_metadata(request.headers.get("x-artifact-kind", "artifact"),
        request.headers.get("idempotency-key", ""), request.headers.get("content-type", ""),
        request.headers.get("x-source-event-at"), service.config)
    payload = await bounded_body(request, service.config.max_payload_bytes)
    receipt, created = await service.repository.admit(principal, metadata, payload)
    return JSONResponse(jsonable_encoder({**public_receipt(receipt), "created": created}),
                        status_code=202 if created else 200)


@router.get("/artifacts")
async def list_artifacts(request: Request, principal: Principal = Depends(require_principal)):
    query = request.query_params.get("query", "")
    try:
        limit = int(request.query_params.get("limit", "50"))
    except ValueError as exc:
        raise RetentionError("invalid_limit", 422) from exc
    if len(query) > 200 or not 1 <= limit <= 100:
        raise RetentionError("invalid_query", 422)
    rows = await get_retention_service(request).repository.list(principal, query=query, limit=limit)
    return {"contract_version": "retention.v1", "items": [public_receipt(row) for row in rows],
            "snapshot_complete": False, "cursor": None}


@router.get("/artifacts/{artifact_id}")
async def artifact(request: Request, artifact_id: str, principal: Principal = Depends(require_principal)):
    row = await get_retention_service(request).repository.get(principal, canonical_uuid(artifact_id))
    return public_receipt(row)


@router.get("/artifacts/{artifact_id}/content")
async def content(request: Request, artifact_id: str, principal: Principal = Depends(require_principal)):
    receipt, payload = await get_retention_service(request).content(principal, artifact_id)
    return Response(payload, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{receipt["artifact_id"]}.bin"',
        "X-Artifact-SHA256": receipt["sha256"], "X-Artifact-Id": receipt["artifact_id"],
        "X-Retention-Contract": "retention.v1"})


@router.get("/jobs/{job_id}")
async def job(request: Request, job_id: str, principal: Principal = Depends(require_principal)):
    row = await get_retention_service(request).repository.get_job(principal, canonical_uuid(job_id))
    return public_receipt(row)


@router.post("/jobs/{job_id}/cancel")
async def cancel(request: Request, job_id: str, principal: Principal = Depends(require_principal)):
    return await get_retention_service(request).repository.cancel(principal, canonical_uuid(job_id))


@router.delete("/artifacts/{artifact_id}")
async def delete(request: Request, artifact_id: str, principal: Principal = Depends(require_principal)):
    return await get_retention_service(request).repository.delete(principal, canonical_uuid(artifact_id))


@router.post("/memories")
async def remember(request: Request, principal: Principal = Depends(require_principal)):
    body = await bounded_body(request, 12000)
    try:
        value = json.loads(body)
        if not isinstance(value, dict) or set(value) != {"artifact_id", "summary"}:
            raise ValueError("invalid memory")
    except (ValueError, UnicodeError) as exc:
        raise RetentionError("invalid_memory_request", 422) from exc
    return await get_retention_service(request).remember(principal, canonical_uuid(value["artifact_id"]), value["summary"])


@router.get("/memories/{memory_id}")
async def recall(request: Request, memory_id: str, principal: Principal = Depends(require_principal)):
    return await get_retention_service(request).recall(principal, memory_id)


@router.get("/memories")
async def memory_search(request: Request, principal: Principal = Depends(require_principal)):
    query = request.query_params.get("query", "")
    try:
        limit = int(request.query_params.get("limit", "20"))
    except ValueError as exc:
        raise RetentionError("invalid_limit", 422) from exc
    if len(query) > 200 or not 1 <= limit <= 20:
        raise RetentionError("invalid_query", 422)
    service = get_retention_service(request)
    candidates = await service.repository.memory_list(principal, query=query, limit=limit)
    return {"contract_version": "retention.v1", "items": [
        await service.recall(principal, str(row["memory_id"])) for row in candidates],
        "similarity_grants_access": False}


@router.get("/artifacts/{artifact_id}/events")
async def events(request: Request, artifact_id: str, principal: Principal = Depends(require_principal)):
    identifier = canonical_uuid(artifact_id)
    service = get_retention_service(request)
    await service.repository.get(principal, identifier)

    async def stream():
        for _ in range(12):
            if await request.is_disconnected():
                return
            try:
                # Revalidate expiry and live membership every bounded event iteration.
                current = await require_principal(request)
                row = await service.repository.get(current, identifier)
                yield "event: receipt\ndata: " + json.dumps(jsonable_encoder(public_receipt(row))) + "\n\n"
            except Exception:
                yield 'event: unavailable\ndata: {"error":"authorization_or_artifact_unavailable"}\n\n'
                return
            await asyncio.sleep(5)
    return StreamingResponse(stream(), media_type="text/event-stream")
