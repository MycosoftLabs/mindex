"""Customer JWT routes and separately credentialed, owner-bound worker routes."""
from __future__ import annotations

import asyncio
import hmac
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse

from ..formspace.contracts import (Admission, Claim, Computed, Failed, FormSpaceError,
                                  Lease, MAX_REQUEST_BYTES, MAX_RESULT_BYTES, receipt)
from ..formspace.repository import FormSpaceRepository
from ..formspace.service import FormSpaceService, strict_json

class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                response = await original(request)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Authorization, X-Tenant-Id, X-Project-Id"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        return handler


router = APIRouter(prefix="/formspace/v1", tags=["FormSpace durable experiments"], route_class=PrivateRoute)


def error(exc):
    if isinstance(exc, FormSpaceError) or (type(exc).__module__.startswith("mindex_api.retention")
                                         and hasattr(exc, "code") and hasattr(exc, "status")):
        return HTTPException(exc.status, detail={"code": exc.code})
    return HTTPException(503, detail={"code": "formspace_unavailable"})


async def principal(request: Request):
    try:
        from .retention import require_principal
        return await require_principal(request)
    except HTTPException:
        raise
    except Exception as exc:
        raise error(exc) from exc


async def service(request: Request):
    injected = getattr(request.app.state, "formspace_service", None)
    if injected is not None:
        return injected
    if os.environ.get("FORMSPACE_DURABLE_ENABLED", "").lower() != "true":
        raise HTTPException(503, detail={"code": "formspace_disabled"})
    try:
        from .retention import get_retention_service
        from ..retention.contracts import Principal
        retained = get_retention_service(request)
        value = FormSpaceService(FormSpaceRepository(retained.repository, Principal), retained,
                                 os.environ.get("FORMSPACE_ENGINE_CODE_SHA256", ""))
        value.enabled()
        return value
    except Exception as exc:
        raise error(exc) from exc


async def worker(request: Request):
    configured = os.environ.get("FORMSPACE_WORKER_TOKEN", "")
    supplied = request.headers.get("X-FormSpace-Worker-Token", "")
    if len(configured) < 32:
        raise HTTPException(503, detail={"code": "worker_unconfigured"})
    if len(supplied) > 512 or not hmac.compare_digest(configured, supplied):
        raise HTTPException(401, detail={"code": "worker_unauthorized"})


async def body(request, model, limit=MAX_REQUEST_BYTES):
    raw = bytearray()
    if request.headers.get("content-encoding", "identity") != "identity":
        raise HTTPException(415, detail={"code": "unsupported_content_encoding"})
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > limit:
                    raise HTTPException(413, detail={"code": "payload_too_large"})
                raw.extend(chunk)
    except TimeoutError as exc:
        raise HTTPException(408, detail={"code": "request_timeout"}) from exc
    try:
        return model.model_validate(strict_json(bytes(raw))).model_dump()
    except (ValueError, TypeError, ValidationError, RecursionError) as exc:
        raise HTTPException(422, detail={"code": "invalid_request"}) from exc


async def call(method, *args, **kwargs):
    try:
        return await method(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise error(exc) from exc


@router.post("/jobs")
async def admit(request: Request, response: Response, owner=Depends(principal), app=Depends(service)):
    data = await body(request, Admission)
    result, created = await call(app.repository.admit, owner,
                                request.headers.get("Idempotency-Key", ""), data["request"])
    response.status_code = 202 if created else 200
    response.headers["Cache-Control"] = "no-store"
    return {"schema": "formspace.job/v1", "job": result}


@router.get("/jobs")
async def jobs(limit: int = Query(50, ge=1, le=100), owner=Depends(principal), app=Depends(service)):
    return {"schema": "formspace.jobs/v1", "jobs": await call(app.repository.list, owner, limit)}


@router.get("/jobs/{job_id}")
async def job(job_id: UUID, owner=Depends(principal), app=Depends(service)):
    return {"schema": "formspace.job/v1", "job": receipt(await call(app.repository.get, owner, str(job_id)))}


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: UUID, owner=Depends(principal), app=Depends(service)):
    return {"schema": "formspace.job/v1", "job": await call(app.repository.cancel, owner, str(job_id))}


@router.post("/jobs/{job_id}/memory")
async def memory(job_id: UUID, owner=Depends(principal), app=Depends(service)):
    return {"schema": "formspace.job/v1", "job": await call(app.remember, owner, str(job_id))}


@router.get("/jobs/{job_id}/result")
async def result(job_id: UUID, owner=Depends(principal), app=Depends(service)):
    payload, sha, version = await call(app.result, owner, str(job_id))
    return Response(payload, media_type="application/json", headers={"Cache-Control": "no-store",
        "X-Content-SHA256": sha, "X-Artifact-Version": version,
        "Content-Disposition": 'attachment; filename="formspace-result.json"'})


@router.get("/jobs/{job_id}/input")
async def input_manifest(job_id: UUID, owner=Depends(principal), app=Depends(service)):
    payload, sha = await call(app.input, owner, str(job_id))
    return Response(payload, media_type="application/json", headers={"Cache-Control": "no-store",
        "X-Input-SHA256": sha, "X-Input-Durability": "postgres_committed",
        "Content-Disposition": 'attachment; filename="formspace-input.json"'})


@router.post("/worker/claim", dependencies=[Depends(worker)])
async def claim(request: Request, app=Depends(service)):
    data = await body(request, Claim, 4096)
    return await call(app.repository.claim, data["worker_id"])


@router.post("/worker/jobs/{job_id}/heartbeat", dependencies=[Depends(worker)])
async def heartbeat(job_id: UUID, request: Request, app=Depends(service)):
    data = await body(request, Lease, 4096)
    return await call(app.repository.heartbeat, str(job_id), data)


@router.post("/worker/jobs/{job_id}/computed", dependencies=[Depends(worker)])
async def computed(job_id: UUID, request: Request, app=Depends(service)):
    # JSON string escaping may at most double normal canonical JSON bytes here.
    data = await body(request, Computed, MAX_RESULT_BYTES * 2 + 4096)
    return await call(app.computed, str(job_id), data)


@router.post("/worker/jobs/{job_id}/reconcile", dependencies=[Depends(worker)])
async def reconcile(job_id: UUID, request: Request, app=Depends(service)):
    data = await body(request, Lease, 4096)
    return await call(app.reconcile, str(job_id), data)


@router.post("/worker/jobs/{job_id}/fail", dependencies=[Depends(worker)])
async def fail(job_id: UUID, request: Request, app=Depends(service)):
    data = await body(request, Failed, 4096)
    return await call(app.repository.fail, str(job_id), data, data["error_code"])
