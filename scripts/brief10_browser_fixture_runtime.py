"""Loopback-only app-state fixture adapters for the Brief10 rendered browser gate."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mindex_api.main import app
from mindex_api.ledger.provenance import TrustedSourceKey
from mindex_api.retention.contracts import Principal, RetentionConfig, RetentionError
from mindex_api.retention.identity import IdentityVerifier
from mindex_api.retention.service import RetentionService


def configure() -> None:
    fixture_path = Path(os.environ["BRIEF10_FIXTURE_FILE"])
    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    users = fixtures["users"]
    principals = {
        user["label"]: Principal(fixtures["issuer"], user["subject"], user["tenant_id"], user["project_id"])
        for user in users
    }
    rows = {
        user["artifact_id"]: {
            "artifact_id": user["artifact_id"], "job_id": str(uuid4()), "kind": "artifact",
            "sha256": user["artifact_sha256"], "byte_length": len(user["artifact_payload_utf8"].encode()),
            "media_type": "text/plain", "state": "verified", "tenant_id": user["tenant_id"],
            "project_id": user["project_id"], "object_version": "local-fixture-v1",
            "owner_issuer": fixtures["issuer"], "owner_subject": user["subject"],
        }
        for user in users
    }
    payloads = {user["artifact_id"]: user["artifact_payload_utf8"].encode() for user in users}
    allowed = {(p.issuer, p.subject, p.tenant_id, p.project_id) for p in principals.values()}

    class FixtureRepository:
        async def require_membership(self, principal):
            if (principal.issuer, principal.subject, principal.tenant_id, principal.project_id) not in allowed:
                raise RetentionError("membership_required", 403)

        async def get(self, principal, artifact_id):
            await self.require_membership(principal)
            row = rows.get(artifact_id)
            if row is None or any(row[key] != getattr(principal, attr) for key, attr in (
                ("owner_issuer", "issuer"), ("owner_subject", "subject"),
                ("tenant_id", "tenant_id"), ("project_id", "project_id"),
            )):
                raise RetentionError("artifact_not_found", 404)
            return dict(row)

    class FixtureObjectStore:
        def read(self, row):
            payload = payloads.get(row["artifact_id"])
            if payload is None:
                raise RetentionError("archive_unavailable", 503)
            return payload

    class OperatorFixture:
        async def __call__(self, principal):
            user_a = principals["User A"]
            exact_scope = (principal.issuer, principal.subject, principal.tenant_id, principal.project_id)
            user_a_scope = (user_a.issuer, user_a.subject, user_a.tenant_id, user_a.project_id)
            return {"allowed": True, "policy_version": "brief10-local-e2e-policy-v1"} if exact_scope == user_a_scope else {"allowed": False}

    async def public_keys():
        return fixtures["jwks"]

    verifier = IdentityVerifier(issuer=fixtures["issuer"], audience=fixtures["audience"],
        key_provider=public_keys, enabled=True, clock_skew_seconds=0)
    config = RetentionConfig(enabled=True, max_payload_bytes=1024 * 1024,
        max_pending_bytes=4 * 1024 * 1024, max_pending_count=50, lease_seconds=120, retention_days=30)
    app.state.retention_service = RetentionService(FixtureRepository(), verifier, FixtureObjectStore(), config)
    source_keys = {}
    for user in users:
        principal = principals[user["label"]]
        source_keys[(principal.subject, principal.project_id, user["source_key_id"])] = TrustedSourceKey(
            key_id=user["source_key_id"], public_key_hex=user["source_public_key_hex"],
            issuer=principal.issuer, subject=principal.subject, tenant_id=principal.tenant_id,
            project_id=principal.project_id,
            valid_from=datetime.fromisoformat(user["registration"]["source"]["signed_at"].replace("Z", "+00:00")),
            valid_until=datetime.fromisoformat(user["registration"]["source"]["expires_at"].replace("Z", "+00:00")),
        )

    async def source_key(principal, key_id):
        return source_keys.get((principal.subject, principal.project_id, key_id))

    app.state.ledger_source_key_resolver = source_key
    app.state.ledger_operator_resolver = OperatorFixture()


if __name__ == "__main__":
    import uvicorn

    if os.environ.get("BRIEF10_BIND_HOST") != "127.0.0.1":
        raise SystemExit("BRIEF10_BIND_HOST must be explicitly set to 127.0.0.1")
    configure()
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("BRIEF10_MINDEX_PORT", "8011")),
                log_level="info", access_log=True)
