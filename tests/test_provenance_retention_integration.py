"""Cross-brief ASGI contract with REAL shared09 JWT verifier/service.

Uses the integrated brief09 retention.v1 package. Membership/object adapters are
explicit fixtures; PostgreSQL, Supabase service and S3 deployment remain separate.
"""
from __future__ import annotations

import importlib
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from mindex_api import provenance_access as access
from test_provenance_api import PREFIX, boundary


def shared_modules():
    try:
        contracts = importlib.import_module("mindex_api.retention.contracts")
        identity = importlib.import_module("mindex_api.retention.identity")
        service = importlib.import_module("mindex_api.retention.service")
        routes = importlib.import_module("mindex_api.routers.retention")
    except ImportError:
        pytest.fail("shared09 source must be integrated before running provenance tests")
    return contracts, identity, service, routes


@pytest.mark.asyncio
async def test_verified_original_jwt_scoped_artifact_to_ledger_and_revocation(boundary, monkeypatch):
    contracts, identity, services, routes = shared_modules()
    jwt = pytest.importorskip("jwt")
    principal = boundary.principal
    subject_two, project_two = str(uuid4()), str(uuid4())
    memberships = {(principal.subject, principal.project_id), (subject_two, project_two)}
    payload = b"PRIVATE TELEMETRY AND IP BYTES - MUST NOT BE PUBLIC"
    artifact_id = str(boundary.registration.evidence.artifact_ids[0])
    artifact_hash = boundary.registration.evidence.artifact_digests[artifact_id]
    row = dict(artifact_id=artifact_id, job_id=str(uuid4()), kind="artifact", sha256=artifact_hash,
               byte_length=len(payload), media_type="application/octet-stream", state="verified",
               tenant_id=principal.tenant_id, project_id=principal.project_id, object_version="fixture-v1")

    async def membership(value):
        if value.tenant_id != principal.tenant_id or (value.subject, value.project_id) not in memberships:
            raise contracts.RetentionError("membership_required", 403)

    async def get(value, identifier):
        await membership(value)
        if value.subject != principal.subject or value.project_id != principal.project_id or identifier != artifact_id:
            raise contracts.RetentionError("artifact_not_found", 404)
        return row.copy()

    key = ec.generate_private_key(ec.SECP256R1())
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid="offline-idp-key", alg="ES256", use="sig")
    async def public_keys():
        return {"keys": [jwk]}
    verifier = identity.IdentityVerifier(issuer=principal.issuer, audience="authenticated",
        enabled=True, key_provider=public_keys, clock_skew_seconds=0)
    repository = SimpleNamespace(require_membership=membership, get=get)
    service = services.RetentionService(repository, verifier, SimpleNamespace(read=lambda _: payload),
                                       contracts.RetentionConfig(enabled=True))
    boundary.app.state.retention_service = service
    boundary.app.include_router(routes.router, prefix="/api/mindex")
    monkeypatch.setattr(access, "retention_module", lambda: routes)
    def token(subject=principal.subject, **changes):
        now = int(time.time())
        claims = {"iss": principal.issuer, "aud": "authenticated", "sub": subject,
                  "iat": now - 5, "exp": now + 300, "role": "authenticated", "is_anonymous": False}
        claims.update(changes)
        return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "offline-idp-key"})
    def headers(bearer, project=principal.project_id):
        return {"Authorization": "Bearer " + bearer, "X-Tenant-Id": principal.tenant_id,
                "X-Project-Id": project}
    owner_headers = headers(token())
    result = await boundary.client.post(PREFIX + "/records", headers=owner_headers,
                                        json=boundary.registration.model_dump(mode="json"))
    assert result.status_code == 201, result.text
    record_id = result.json()["id"]
    assert result.json()["state"] == "registered" and result.json()["onchain_confirmed"] is False
    async def source_key(value, key_id):
        return boundary.key if value.__dict__ == principal.scope() and key_id == boundary.key.key_id else None
    boundary.app.state.ledger_source_key_resolver = source_key
    validated = await boundary.client.post(PREFIX + "/records/" + record_id + "/validate",
        headers=owner_headers, json={"idempotency_key": "shared-validate"})
    assert validated.status_code == 200, validated.text
    assert validated.json()["state"] == "validated"
    async def operator(_):
        return {"allowed": True, "policy_version": "fixture-review-v1"}
    boundary.app.state.ledger_operator_resolver = operator
    approved = await boundary.client.post(PREFIX + "/records/" + record_id + "/approve", headers=owner_headers,
        json={"idempotency_key": "shared-approve", "policy_version": "fixture-review-v1",
              "privacy_review": "accepted", "equality_leak_review": "accepted"})
    assert approved.status_code == 200 and approved.json()["state"] == "approved", approved.text
    assert approved.json()["onchain_confirmed"] is False
    # Qualify the real retention.v1 content route after the provenance lifecycle.
    # The route returns the exact archived bytes only for the verified scope.
    artifact_path = f"/api/mindex/retention/v1/artifacts/{artifact_id}/content"
    retained = await boundary.client.get(artifact_path, headers=owner_headers)
    assert retained.status_code == 200
    assert retained.content == payload
    assert retained.headers["x-artifact-sha256"] == artifact_hash
    assert retained.headers["x-artifact-id"] == artifact_id
    assert retained.headers["cache-control"] == "private, no-store"
    # Wrong tenant, another authorized user/project, and a changed project
    # cannot retrieve this artifact or its ledger metadata.
    wrong_tenant = {**owner_headers, "X-Tenant-Id": str(uuid4())}
    assert (await boundary.client.get(artifact_path, headers=wrong_tenant)).status_code == 403
    assert (await boundary.client.get(artifact_path, headers=headers(token(subject_two), project_two))).status_code == 404
    assert (await boundary.client.get(artifact_path, headers=headers(token(), project_two))).status_code == 403
    # Same valid token, unrelated selected project never creates membership.
    assert (await boundary.client.get(PREFIX + "/records", headers=headers(token(), project_two))).status_code == 403
    unrelated = headers(token(subject_two), project_two)
    assert (await boundary.client.get(PREFIX + "/records", headers=unrelated)).json() == []
    assert (await boundary.client.get(PREFIX + "/records/" + record_id, headers=unrelated)).status_code == 404
    assert (await boundary.client.post(PREFIX + "/records", headers=unrelated,
            json=boundary.registration.model_dump(mode="json"))).status_code == 404
    for invalid in (token(exp=int(time.time()) - 1), token(aud="other"),
                    token(iss="https://impostor.example/auth/v1"), token(role="service_role"),
                    token(is_anonymous=True), token()[:-8] + "invalidx"):
        denied = await boundary.client.get(PREFIX + "/records", headers=headers(invalid))
        assert denied.status_code == 401, denied.text
        assert denied.headers["cache-control"] == "private, no-store"
    # Raw service key and forged owner are never equivalent to the JWT.
    denied = await boundary.client.get(PREFIX + "/records", headers={"X-Internal-Token": "fixture",
        "X-User-Id": principal.subject, "X-Tenant-Id": principal.tenant_id, "X-Project-Id": principal.project_id})
    assert denied.status_code == 401
    memberships.remove((principal.subject, principal.project_id))
    assert (await boundary.client.get(PREFIX + "/records/" + record_id, headers=owner_headers)).status_code == 403
    revoked = await boundary.client.get(artifact_path, headers=owner_headers)
    assert revoked.status_code == 403, revoked.text
    assert revoked.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_integrated_application_mounts_retention_and_provenance_fail_closed(monkeypatch):
    import httpx
    import mindex_api.main as application
    from fastapi import FastAPI
    from mindex_api.routers.provenance import router as provenance_router
    from mindex_api.routers.retention import router as retention_router

    combined = FastAPI()
    combined.include_router(retention_router, prefix="/api/mindex")
    combined.include_router(provenance_router, prefix="/api/mindex")
    assert any("/retention/v1" in route.path for route in combined.routes)
    assert any("/ledger/provenance/v1" in route.path for route in combined.routes)
    assert len([route for route in application.app.routes if "/retention/v1" in route.path]) == 12
    assert len([route for route in application.app.routes if "/ledger/provenance/v1" in route.path]) == 12
    monkeypatch.delenv("RETENTION_ENABLED", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application.app),
                                 base_url="http://fixture") as client:
        response = await client.get("/api/mindex/retention/v1/principal", headers={
            "Authorization": "Bearer fixture", "X-Tenant-Id": str(uuid4()),
            "X-Project-Id": str(uuid4())})
    assert response.status_code == 503
    assert response.json()["error"] == "retention_unavailable"
    assert response.headers["cache-control"] == "private, no-store"
