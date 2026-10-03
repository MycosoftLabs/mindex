"""Actual ASGI + durable SQL fixture boundary, no chain or network provider."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mindex_api import provenance_access as access
from mindex_api.dependencies import get_db_session
from mindex_api.ledger import provenance as domain
from mindex_api.ledger.provenance_store import metadata, records
from mindex_api.routers.provenance import router

PREFIX = "/api/mindex/ledger/provenance/v1"


@pytest_asyncio.fixture
async def boundary(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "ledger.db"),
                                 execution_options={"schema_translate_map": {"ledger": None}})
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router, prefix="/api/mindex")
    principal = domain.ProvenancePrincipal("https://identity.example/auth/v1", str(uuid4()),
                                           str(uuid4()), str(uuid4()))
    other = replace(principal, subject=str(uuid4()))
    second_project = replace(principal, project_id=str(uuid4()))
    body = b"PRIVATE TELEMETRY AND IP BYTES - MUST NOT BE PUBLIC"
    artifact_id = str(uuid4())
    digest = hashlib.sha256(body).hexdigest()
    calls = []
    active = {"value": True}

    async def membership(value):
        calls.append(("membership", value.subject, value.project_id))
        if not active["value"]:
            raise HTTPException(403, "membership_revoked")

    async def content(value, identifier):
        await membership(value)
        if value != principal or identifier != artifact_id:
            raise HTTPException(404, "artifact_not_found")
        calls.append(("content", identifier))
        return {"sha256": digest}, body

    service = SimpleNamespace(content=content, repository=SimpleNamespace(require_membership=membership))

    async def authenticate(request):
        # Explicit fixture authority for boundary tests. Real JWT is exercised
        # in test_provenance_retention_integration with brief09 verifier.
        value = {"Bearer owner": principal, "Bearer other": other,
                 "Bearer project-two": second_project}.get(request.headers.get("authorization"))
        if value is None:
            raise HTTPException(401, "authentication_required")
        await membership(value)
        return value

    module = SimpleNamespace(require_principal=authenticate, get_retention_service=lambda _: service)
    monkeypatch.setattr(access, "retention_module", lambda: module)
    async def session():
        async with sessions() as db:
            yield db
    app.dependency_overrides[get_db_session] = session
    private_key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    key = domain.TrustedSourceKey("source-key-1", private_key.public_key().public_bytes_raw().hex(),
        **principal.scope(), valid_from=now - timedelta(hours=1), valid_until=now + timedelta(hours=2))
    async def resolver(value, key_id):
        return key if value == principal and key_id == key.key_id else None
    app.state.ledger_source_key_resolver = resolver
    evidence = domain.EvidenceEnvelope(source_class="telemetry", artifact_ids=[artifact_id],
        artifact_digests={artifact_id: digest}, description="Private fixture", rights={
            "license_id": "private-test", "consent_reference": "fixture-consent", "ownership_verified": False})
    source = domain.SourceSignature(key_id=key.key_id, signature_hex="0" * 128,
        signed_at=now, expires_at=now + timedelta(minutes=30))
    source.signature_hex = private_key.sign(domain.source_signing_message(principal, evidence, source)).hex()
    registration = domain.RegisterRequest(idempotency_key="register-fixture-1", evidence=evidence,
        source=source, content_hash=hashlib.sha256(domain.canonical_record_bytes(principal, evidence, source)).hexdigest())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        yield SimpleNamespace(client=client, app=app, sessions=sessions, principal=principal,
                              registration=registration, active=active, service=service,
                              calls=calls, key=key, engine=engine)
    await engine.dispose()


async def registered(boundary):
    result = await boundary.client.post(PREFIX + "/records", json=boundary.registration.model_dump(mode="json"),
                                        headers={"Authorization": "Bearer owner"})
    assert result.status_code == 201, result.text
    return result.json()


@pytest.mark.asyncio
async def test_local_lifecycle_readback_and_durable_operator_policy(boundary):
    record = await registered(boundary)
    assert record["state_label"] == "recorded"
    assert record["onchain_confirmed"] is False and record["transaction_id"] is None
    headers = {"Authorization": "Bearer owner"}
    path = PREFIX + "/records/" + record["id"]
    checked = await boundary.client.post(path + "/validate", headers=headers,
                                         json={"idempotency_key": "validate-1"})
    assert checked.status_code == 200, checked.text
    assert checked.json()["state"] == "validated"
    payload = {"idempotency_key": "approve-1", "policy_version": "policy-v1",
               "privacy_review": "accepted", "equality_leak_review": "accepted"}
    unavailable = await boundary.client.post(path + "/approve", headers=headers, json=payload)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "ledger_operator_authority_unavailable"
    async def operator(_):
        return {"allowed": True, "policy_version": "policy-v1"}
    boundary.app.state.ledger_operator_resolver = operator
    approved = await boundary.client.post(path + "/approve", headers=headers, json=payload)
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["actor"] == boundary.principal.scope()
    assert approved.json()["approval"]["public_broadcast_authorized"] is False
    queued = await boundary.client.get(PREFIX + "/queue", headers=headers)
    assert queued.json()[0]["status"] == "manual_submission_disabled"
    # New connection retrieves committed evidence and events.
    await boundary.engine.dispose()
    readback = await boundary.client.get(path, headers=headers)
    assert readback.json()["state"] == "approved"
    audit = await boundary.client.get(path + "/events", headers=headers)
    assert [item["kind"] for item in audit.json()] == ["register", "validate", "approve"]
    assert audit.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_cross_user_project_record_event_lineage_and_artifact_denial(boundary):
    record = await registered(boundary)
    for token in ("other", "project-two"):
        headers = {"Authorization": "Bearer " + token, "X-User-Id": boundary.principal.subject,
                   "X-Role": "ledger_operator"}
        for suffix in ("", "/events", "/lineage"):
            result = await boundary.client.get(PREFIX + "/records/" + record["id"] + suffix, headers=headers)
            assert result.status_code == 404
        assert (await boundary.client.get(PREFIX + "/records", headers=headers)).json() == []
        result = await boundary.client.post(PREFIX + "/records", headers=headers,
                                            json=boundary.registration.model_dump(mode="json"))
        assert result.status_code == 404


@pytest.mark.asyncio
async def test_replay_conflict_unknown_fields_and_invalid_hash(boundary):
    record = await registered(boundary)
    replay = await registered(boundary)
    assert replay["id"] == record["id"]
    body = boundary.registration.model_dump(mode="json")
    body["evidence"]["description"] = "modified"
    result = await boundary.client.post(PREFIX + "/records", json=body, headers={"Authorization": "Bearer owner"})
    assert result.status_code == 409
    body["idempotency_key"] = "new-registration"
    result = await boundary.client.post(PREFIX + "/records", json=body, headers={"Authorization": "Bearer owner"})
    assert result.status_code == 422
    body["actor"] = "forged-operator"
    assert (await boundary.client.post(PREFIX + "/records", json=body)).status_code == 401
    assert (await boundary.client.post(PREFIX + "/records", json=body,
            headers={"Authorization": "Bearer owner"})).status_code == 422


@pytest.mark.asyncio
async def test_no_public_submission_even_for_operator_and_spoofed_fixture(boundary):
    record = await registered(boundary)
    for suffix in ("submit", "verify"):
        result = await boundary.client.post(PREFIX + "/records/" + record["id"] + "/" + suffix,
            headers={"Authorization": "Bearer owner", "X-Role": "ledger_operator"},
            json={"adapter": "offline_fixture", "confirmed": True, "metadata": "private"})
        assert result.status_code == 503
        assert result.json()["error"] == "public_proof_adapter_not_qualified"
    assert (await boundary.client.get(PREFIX + "/records/" + record["id"],
            headers={"Authorization": "Bearer owner"})).json()["state"] == "registered"


@pytest.mark.asyncio
async def test_revocation_during_artifact_io_prevents_registration(boundary):
    original = boundary.service.content
    async def revoke(*args):
        result = await original(*args)
        boundary.active["value"] = False
        return result
    boundary.service.content = revoke
    result = await boundary.client.post(PREFIX + "/records", headers={"Authorization": "Bearer owner"},
                                        json=boundary.registration.model_dump(mode="json"))
    assert result.status_code == 403
    async with boundary.sessions() as db:
        assert (await db.execute(select(records.c.id))).all() == []


@pytest.mark.asyncio
async def test_bounded_body_direct_auth_and_closed_missing_shared_contract(boundary, monkeypatch):
    for headers in ({}, {"X-Internal-Token": "fixture-service", "X-User-Id": boundary.principal.subject}):
        response = await boundary.client.get(PREFIX + "/records", headers=headers)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"
    assert (await boundary.client.post(PREFIX + "/records", content=b"x" * 65537)).status_code == 413
    assert (await boundary.client.post(PREFIX + "/records", content=b"{}",
                                      headers={"Content-Encoding": "gzip"})).status_code == 415
    def unavailable():
        raise HTTPException(503, "shared_retention_contract_unavailable")
    monkeypatch.setattr(access, "retention_module", unavailable)
    assert (await boundary.client.get(PREFIX + "/records")).json() == {"error": "shared_retention_contract_unavailable"}
