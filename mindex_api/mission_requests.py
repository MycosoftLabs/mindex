"""Private mission requests. Real PostgreSQL only; no booking/dispatch side effects."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text


class MissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    purpose: str = Field(min_length=12, max_length=2000)
    task: str = Field(min_length=12, max_length=2000)
    deliverables: str = Field(min_length=8, max_length=1200)
    location_label: str = Field(min_length=2, max_length=160)
    latitude: float = Field(ge=-85, le=85, allow_inf_nan=False, strict=True)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)
    radius_m: int = Field(ge=0, le=50000, strict=True)
    access_notes: str = Field(default="", max_length=1000)
    privacy_consent: bool = Field(strict=True)

    @field_validator("privacy_consent")
    @classmethod
    def consent(cls, value):
        if value is not True:
            raise ValueError("Location and mission retention consent required")
        return value


class QuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    amount_minor: int = Field(ge=1, le=100000000, strict=True)
    currency: str = Field(pattern="^usd$")
    scope: str = Field(min_length=20, max_length=2000)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value.astimezone(timezone.utc)


class CheckoutBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: UUID
    provider_session: str = Field(pattern="^cs_[A-Za-z0-9_]+$", max_length=255)


class PaymentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(pattern="^evt_[A-Za-z0-9_]+$", max_length=255)
    event_type: str = Field(pattern="^(paid|failed|expired|refund)$")
    attempt_id: UUID | None = None
    request_id: UUID | None = None
    quote_id: UUID | None = None
    provider_session: str | None = Field(default=None, max_length=255)
    payment_intent: str | None = Field(default=None, pattern="^pi_[A-Za-z0-9_]+$", max_length=255)
    amount_minor: int = Field(ge=0, le=100000000, strict=True)
    currency: str = Field(pattern="^usd$")
    refunded_minor: int = Field(default=0, ge=0, le=100000000, strict=True)


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str


def fail(code, status=503):
    raise HTTPException(status, detail={"code": code})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def verify_delegation(headers, method, path, raw: bytes, role="user", now=None):
    if os.getenv("MISSION_REQUESTS_ENABLED") != "1":
        fail("mission_storage_unconfigured")
    secret = os.getenv("MISSION_REQUEST_DELEGATION_SECRET", "")
    issuer = os.getenv("MISSION_REQUEST_IDENTITY_ISSUER", "")
    if len(secret) < 32 or not issuer:
        fail("mission_identity_unconfigured")
    timestamp = headers.get("x-mission-timestamp", "")
    subject = headers.get("x-mission-subject", "")
    supplied_issuer = headers.get("x-mission-issuer", "")
    supplied_role = headers.get("x-mission-role", "")
    signature = headers.get("x-mission-signature", "")
    try:
        if role == "user":
            subject = str(UUID(subject))
        elif subject != role:
            raise ValueError()
        if supplied_role != role or supplied_issuer != issuer or abs((time.time() if now is None else now) - int(timestamp)) > 60:
            raise ValueError()
    except (ValueError, TypeError):
        fail("mission_identity_invalid", 401)
    payload = "\n".join([timestamp, method.upper(), path, role, issuer, subject, hashlib.sha256(raw).hexdigest()])
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        fail("mission_identity_invalid", 401)
    return Principal(issuer, subject)


def payment_transition(state, event: PaymentEvent, amount, currency, refunded=0):
    if event.currency != currency or event.amount_minor != amount:
        fail("payment_quote_mismatch", 409)
    if event.event_type == "refund":
        if event.refunded_minor > amount:
            fail("refund_amount_invalid", 409)
        refunded = max(refunded, event.refunded_minor)
        if refunded == 0:
            return state, refunded
        return ("refunded" if refunded == amount else "partially_refunded"), refunded
    if state in ("refunded", "partially_refunded"):
        return state, refunded
    if event.event_type == "paid":
        return "paid", refunded
    if state == "paid":
        return state, refunded
    return event.event_type, refunded


class MissionRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    async def owned(db, request_id, principal, lock=False):
        row = (await db.execute(text("""SELECT id, payload, state, created_at FROM mission.request
            WHERE id=CAST(:id AS uuid) AND issuer=:issuer AND owner_subject=CAST(:owner AS uuid)"""
            + (" FOR UPDATE" if lock else "")),
            {"id": str(request_id), "issuer": principal.issuer, "owner": principal.subject})).mappings().first()
        if not row:
            fail("mission_not_found", 404)
        return dict(row)

    async def create(self, principal, key, body: MissionInput):
        payload = body.model_dump()
        sha = digest(payload)
        async with self.sessions() as db, db.begin():
            # Per-owner lock makes rate admission and idempotency atomic across workers.
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:owner, 0))"),
                             {"owner": principal.issuer + ":" + principal.subject})
            params = {"issuer": principal.issuer, "owner": principal.subject, "key": str(key)}
            existing = (await db.execute(text("""SELECT id, payload_sha256 FROM mission.request
                WHERE issuer=:issuer AND owner_subject=CAST(:owner AS uuid) AND idempotency_key=CAST(:key AS uuid)"""), params)).mappings().first()
            if existing:
                if existing["payload_sha256"] != sha:
                    fail("idempotency_conflict", 409)
                return {"id": str(existing["id"]), "duplicate": True, "retention": "postgres_committed"}
            count = (await db.execute(text("""SELECT count(*) FROM mission.request WHERE issuer=:issuer
                AND owner_subject=CAST(:owner AS uuid) AND created_at > now()-interval '1 hour'"""), params)).scalar_one()
            if count >= 10:
                fail("mission_rate_limit", 429)
            request_id = str(uuid4())
            await db.execute(text("""INSERT INTO mission.request(id,issuer,owner_subject,idempotency_key,payload,payload_sha256)
                VALUES(CAST(:id AS uuid),:issuer,CAST(:owner AS uuid),CAST(:key AS uuid),CAST(:payload AS jsonb),:sha)"""),
                {**params, "id": request_id, "payload": json.dumps(payload), "sha": sha})
        return {"id": request_id, "duplicate": False, "retention": "postgres_committed"}

    async def get(self, principal, request_id):
        async with self.sessions() as db:
            row = await self.owned(db, request_id, principal)
            quote = (await db.execute(text("""SELECT id, revision, amount_minor, currency, scope, expires_at
                FROM mission.quote WHERE request_id=CAST(:id AS uuid) ORDER BY revision DESC LIMIT 1"""), {"id": str(request_id)})).mappings().first()
            payment = (await db.execute(text("""SELECT state, refunded_minor FROM mission.payment_attempt
                WHERE request_id=CAST(:id AS uuid) ORDER BY created_at DESC LIMIT 1"""), {"id": str(request_id)})).mappings().first()
            return {**row, "quote": dict(quote) if quote else None, "payment": dict(payment) if payment else None,
                    "mission_status": "not_scheduled", "delivery": "not_available", "retention": "postgres_committed"}

    async def approve_quote(self, request_id, body: QuoteInput, operator):
        if body.expires_at <= datetime.now(timezone.utc):
            fail("quote_expired", 422)
        async with self.sessions() as db, db.begin():
            request = (await db.execute(text("SELECT state FROM mission.request WHERE id=CAST(:id AS uuid) FOR UPDATE"), {"id": str(request_id)})).mappings().first()
            if not request:
                fail("mission_not_found", 404)
            if request["state"] != "submitted":
                fail("mission_not_reviewable", 409)
            # A prepared checkout freezes its quote; do not charge against a silently revised scope.
            if (await db.execute(text("SELECT id FROM mission.payment_attempt WHERE request_id=CAST(:id AS uuid) LIMIT 1"), {"id": str(request_id)})).first():
                fail("quote_locked_by_payment_attempt", 409)
            revision = (await db.execute(text("SELECT COALESCE(max(revision),0)+1 FROM mission.quote WHERE request_id=CAST(:id AS uuid)"), {"id": str(request_id)})).scalar_one()
            quote_id = str(uuid4())
            await db.execute(text("""INSERT INTO mission.quote(id,request_id,revision,amount_minor,currency,scope,expires_at,approved_by)
                VALUES(CAST(:qid AS uuid),CAST(:id AS uuid),:revision,:amount_minor,:currency,:scope,:expires_at,:operator)"""),
                {**body.model_dump(), "qid": quote_id, "id": str(request_id), "revision": revision, "operator": operator})
        return {"quote_id": quote_id, "revision": revision, "status": "approved_quote", "capacity_reserved": False}

    async def reserve_checkout(self, principal, request_id):
        async with self.sessions() as db, db.begin():
            row = await self.owned(db, request_id, principal, True)
            if row["state"] != "submitted":
                fail("mission_not_payable", 409)
            quote = (await db.execute(text("""SELECT * FROM mission.quote WHERE request_id=CAST(:id AS uuid)
                ORDER BY revision DESC LIMIT 1"""), {"id": str(request_id)})).mappings().first()
            if not quote:
                fail("quote_pending_review", 409)
            if quote["expires_at"] <= datetime.now(timezone.utc):
                fail("quote_expired", 409)
            params = {"id": str(uuid4()), "rid": str(request_id), "qid": str(quote["id"]),
                      "amount": quote["amount_minor"], "currency": quote["currency"]}
            await db.execute(text("""INSERT INTO mission.payment_attempt(id,request_id,quote_id,amount_minor,currency)
                VALUES(CAST(:id AS uuid),CAST(:rid AS uuid),CAST(:qid AS uuid),:amount,:currency)
                ON CONFLICT(quote_id) DO NOTHING"""), params)
            attempt = (await db.execute(text("SELECT * FROM mission.payment_attempt WHERE quote_id=CAST(:qid AS uuid)"), params)).mappings().one()
            if attempt["state"] not in ("reserved", "checkout"):
                fail("payment_attempt_terminal", 409)
            return {"attempt_id": str(attempt["id"]), "request_id": str(request_id), "quote_id": str(quote["id"]),
                    "amount_minor": quote["amount_minor"], "currency": quote["currency"],
                    "expires_at": quote["expires_at"], "checkout_expires_at": min(quote["expires_at"], attempt["created_at"] + timedelta(hours=23)), "provider_session": attempt["provider_session"]}

    async def bind_checkout(self, principal, request_id, body: CheckoutBinding):
        async with self.sessions() as db, db.begin():
            await self.owned(db, request_id, principal, True)
            row = (await db.execute(text("""SELECT provider_session,state FROM mission.payment_attempt WHERE
                id=CAST(:attempt AS uuid) AND request_id=CAST(:id AS uuid) FOR UPDATE"""),
                {"attempt": str(body.attempt_id), "id": str(request_id)})).mappings().first()
            if not row:
                fail("payment_attempt_not_found", 404)
            if row["provider_session"] and row["provider_session"] != body.provider_session:
                fail("checkout_binding_conflict", 409)
            await db.execute(text("""UPDATE mission.payment_attempt SET provider_session=:session,
                state=CASE WHEN state='reserved' THEN 'checkout' ELSE state END,updated_at=now()
                WHERE id=CAST(:attempt AS uuid)"""), {"session": body.provider_session, "attempt": str(body.attempt_id)})
        return {"status": "checkout_recorded"}

    async def apply_event(self, event: PaymentEvent):
        if event.event_type in ("paid", "refund") and not event.payment_intent:
            fail("payment_intent_required", 422)
        sha = digest(event.model_dump(mode="json"))
        async with self.sessions() as db, db.begin():
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:event,0))"), {"event": event.event_id})
            prior = (await db.execute(text("SELECT payload_sha256 FROM mission.payment_event WHERE event_id=:event"), {"event": event.event_id})).mappings().first()
            if prior:
                if prior["payload_sha256"] != sha:
                    fail("payment_event_conflict", 409)
                return {"status": "duplicate"}
            if event.event_type == "refund":
                row = (await db.execute(text("SELECT * FROM mission.payment_attempt WHERE payment_intent=:pi FOR UPDATE"), {"pi": event.payment_intent})).mappings().first()
            else:
                row = (await db.execute(text("SELECT * FROM mission.payment_attempt WHERE id=CAST(:id AS uuid) FOR UPDATE"), {"id": str(event.attempt_id) if event.attempt_id else None})).mappings().first()
            if not row:
                fail("payment_binding_pending", 503)
            if event.event_type != "refund":
                if str(row["request_id"]) != str(event.request_id) or str(row["quote_id"]) != str(event.quote_id):
                    fail("payment_identity_mismatch", 409)
                if not event.provider_session or (row["provider_session"] and row["provider_session"] != event.provider_session):
                    fail("payment_session_mismatch", 409)
            if event.payment_intent and row["payment_intent"] and row["payment_intent"] != event.payment_intent:
                fail("payment_intent_mismatch", 409)
            state, refunded = payment_transition(row["state"], event, row["amount_minor"], row["currency"], row["refunded_minor"])
            await db.execute(text("""UPDATE mission.payment_attempt SET state=:state,refunded_minor=:refunded,
                payment_intent=COALESCE(payment_intent,:pi),provider_session=COALESCE(provider_session,:session),updated_at=now() WHERE id=CAST(:id AS uuid)"""),
                {"state": state, "refunded": refunded, "pi": event.payment_intent, "session": event.provider_session, "id": str(row["id"])})
            await db.execute(text("""INSERT INTO mission.payment_event(event_id,attempt_id,payload_sha256,event_type)
                VALUES(:event,CAST(:id AS uuid),:sha,:type)"""), {"event": event.event_id,"id": str(row["id"]),"sha": sha,"type": event.event_type})
        return {"status": "recorded", "payment_state": state, "mission_status": "not_scheduled"}
