"""Internal service-only raw capture; public/non-customer sources by allowlist."""
from __future__ import annotations

import asyncio
import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..auth import require_internal_token
from ..auth.models import CallerIdentity
from ..config import settings
from ..db import async_session_scope
from ..source_capture import CaptureConfig, CaptureError, CaptureRepository, capture_metadata, receipt
from ..source_capture_s3 import create_object_store

router = APIRouter(prefix="/source-captures", tags=["internal-source-capture"])


def capture_repository():
    return CaptureRepository(async_session_scope, CaptureConfig.from_settings(settings))


def unavailable(exc):
    if isinstance(exc, CaptureError):
        return HTTPException(status_code=exc.status, detail=exc.code)
    return HTTPException(status_code=503, detail="capture_storage_unavailable")


@router.post("/{source_id}")
async def capture_source(source_id: str, request: Request,
                         caller: CallerIdentity = Depends(require_internal_token),
                         repository=Depends(capture_repository)):
    try:
        metadata = capture_metadata(source_id, request.headers.get("Idempotency-Key", ""),
            request.headers.get("Content-Type", "application/octet-stream"),
            request.headers.get("Content-Encoding", "identity"),
            request.headers.get("X-Source-Observed-At"), repository.config)
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > repository.config.max_payload_bytes:
                raise CaptureError("payload_size_invalid", 413)
            payload.extend(chunk)
        result, created = await repository.accept(caller.service, metadata, bytes(payload))
        # Even a duplicate remains pending until the worker proves its S3 copy.
        code = 202 if result["state"] != "archived_verified" else 200
        return JSONResponse(jsonable_encoder({**result, "duplicate": not created}), status_code=code,
                            headers={"Cache-Control": "private, no-store"})
    except Exception as exc:
        raise unavailable(exc) from exc


@router.get("/{capture_id}")
async def capture_status(capture_id: uuid.UUID,
                         caller: CallerIdentity = Depends(require_internal_token),
                         repository=Depends(capture_repository)):
    try:
        repository.config.admission()
        row = await repository.get(str(capture_id))
        return JSONResponse(jsonable_encoder(receipt(row)),
                            headers={"Cache-Control": "private, no-store"})
    except Exception as exc:
        raise unavailable(exc) from exc


@router.get("/{capture_id}/raw")
async def capture_raw(capture_id: uuid.UUID,
                      caller: CallerIdentity = Depends(require_internal_token),
                      repository=Depends(capture_repository)):
    try:
        repository.config.admission()
        row = await repository.get(str(capture_id))
        if row["state"] == "archived_verified":
            store = create_object_store(repository.config)
            payload = await asyncio.to_thread(store.read, row)
        else:
            payload = bytes(row["payload"])
            if (len(payload) != row["byte_length"] or
                    hashlib.sha256(payload).hexdigest() != row["sha256"]):
                raise CaptureError("capture_integrity_failed")
        # Deliberately do not set Content-Encoding: the bytes are an archive,
        # not an instruction for a client to decompress/execute upstream data.
        return Response(payload, media_type="application/octet-stream", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{capture_id}.bin"',
            "X-Capture-SHA256": row["sha256"], "X-Capture-State": row["state"],
        })
    except Exception as exc:
        raise unavailable(exc) from exc
