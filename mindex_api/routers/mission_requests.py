"""Internal mission gateway. Private requests never enter public source-capture storage."""
from __future__ import annotations

import hmac
import json
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..auth import require_internal_token
from ..auth.models import CallerIdentity
from ..db import async_session_scope
from ..mission_requests import (CheckoutBinding, MissionInput, MissionRepository, PaymentEvent,
                                QuoteInput, fail, verify_delegation)

router = APIRouter(prefix="/mission-requests", tags=["internal-mission-requests"],
                   dependencies=[Depends(require_internal_token)])


def repository():
    return MissionRepository(async_session_scope)


async def envelope(request: Request, role="user"):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16384:
            fail("mission_payload_too_large", 413)
    principal = verify_delegation(request.headers, request.method, request.url.path, bytes(raw), role)
    try:
        data = json.loads(raw) if raw else {}
    except (ValueError, UnicodeError):
        fail("mission_payload_invalid", 422)
    return principal, data


def reply(value, status=200):
    return JSONResponse(jsonable_encoder(value), status_code=status,
                        headers={"Cache-Control": "private, no-store"})


async def run(operation):
    try:
        return await operation
    except HTTPException:
        raise
    except (ValidationError, ValueError, TypeError):
        fail("mission_payload_invalid", 422)
    except Exception:
        fail("mission_storage_unavailable", 503)


@router.post("")
async def create(request: Request, repo=Depends(repository)):
    principal, data = await envelope(request)
    try:
        if set(data) != {"idempotency_key", "mission"}:
            fail("mission_payload_invalid", 422)
        key = UUID(data["idempotency_key"])
        mission = MissionInput.model_validate(data["mission"])
    except (ValidationError, ValueError, TypeError, KeyError):
        fail("mission_payload_invalid", 422)
    return reply(await run(repo.create(principal, key, mission)), 201)


@router.get("/{request_id}")
async def read(request_id: UUID, request: Request, repo=Depends(repository)):
    principal, _ = await envelope(request)
    return reply(await run(repo.get(principal, request_id)))


@router.post("/{request_id}/quote")
async def approve(request_id: UUID, request: Request, repo=Depends(repository),
                  caller: CallerIdentity = Depends(require_internal_token)):
    _, data = await envelope(request, "operator")
    token = os.getenv("MISSION_REQUEST_OPERATOR_TOKEN", "")
    supplied = request.headers.get("x-mission-operator-token", "")
    if len(token) < 32:
        fail("mission_operator_unconfigured")
    if not hmac.compare_digest(token, supplied):
        fail("mission_operator_forbidden", 403)
    try:
        body = QuoteInput.model_validate(data)
    except (ValidationError, ValueError, TypeError):
        fail("mission_quote_invalid", 422)
    return reply(await run(repo.approve_quote(request_id, body, caller.service or "operator-service")))


@router.post("/{request_id}/checkout")
async def checkout(request_id: UUID, request: Request, repo=Depends(repository)):
    principal, data = await envelope(request)
    if data:
        fail("checkout_body_must_be_empty", 422)
    return reply(await run(repo.reserve_checkout(principal, request_id)))


@router.post("/{request_id}/checkout/bind")
async def bind(request_id: UUID, request: Request, repo=Depends(repository)):
    principal, data = await envelope(request)
    try:
        body = CheckoutBinding.model_validate(data)
    except (ValidationError, ValueError, TypeError):
        fail("checkout_binding_invalid", 422)
    return reply(await run(repo.bind_checkout(principal, request_id, body)))


@router.post("/events/stripe")
async def event(request: Request, repo=Depends(repository)):
    _, data = await envelope(request, "webhook")
    try:
        body = PaymentEvent.model_validate(data)
    except (ValidationError, ValueError, TypeError):
        fail("payment_event_invalid", 422)
    return reply(await run(repo.apply_event(body)))
