"""Private, owner-scoped MINDEX provenance lifecycle.

Authentication, membership, retained artifacts and trusted source key discovery
belong to shared identity/retention (brief 09). This module only consumes trusted
server-derived inputs. Each mutation owns its transaction and durable audit event.
No operation signs or broadcasts a chain transaction.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from .provenance_proof import Chain, FixtureProof, StrictModel, public_commitment, verify_fixture
from .provenance_store import events, queue, receipts, records


class ProvenanceError(Exception):
    def __init__(self, code: str, status_code: int = 422):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class ProvenancePrincipal:
    issuer: str
    subject: str
    tenant_id: str
    project_id: str
    roles: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        for name, maximum in (("issuer", 512), ("subject", 256), ("tenant_id", 128), ("project_id", 128)):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ProvenanceError("invalid_principal_scope", 401)

    def scope(self) -> dict[str, str]:
        return {key: getattr(self, key) for key in ("issuer", "subject", "tenant_id", "project_id")}


@dataclass(frozen=True)
class TrustedSourceKey:
    key_id: str
    public_key_hex: str
    issuer: str
    subject: str
    tenant_id: str
    project_id: str
    valid_from: datetime
    valid_until: datetime
    revoked: bool = False
    purpose: str = "provenance-source"


class RightsMetadata(StrictModel):
    license_id: str = Field(min_length=1, max_length=256)
    consent_reference: str = Field(min_length=1, max_length=512)
    association_claim: str = Field(default="", max_length=1000)
    ownership_verified: Literal[False] = False


class PrivacyMetadata(StrictModel):
    classification: Literal["private", "restricted"] = "private"
    public_commitment_allowed: bool = False


class EvidenceEnvelope(StrictModel):
    schema_version: Literal["mindex-provenance-v1"] = "mindex-provenance-v1"
    source_class: Literal["laboratory", "telemetry", "synthetic", "model_result", "document"]
    artifact_ids: list[uuid.UUID] = Field(min_length=1, max_length=32)
    artifact_digests: dict[str, str] = Field(min_length=1, max_length=32)
    parent_record_ids: list[uuid.UUID] = Field(default_factory=list, max_length=32)
    description: str = Field(default="", max_length=1000)
    rights: RightsMetadata
    privacy: PrivacyMetadata = Field(default_factory=PrivacyMetadata)

    @model_validator(mode="after")
    def exact_artifacts(self):
        ids = [str(value) for value in self.artifact_ids]
        if len(ids) != len(set(ids)) or len(self.parent_record_ids) != len(set(self.parent_record_ids)):
            raise ValueError("duplicate_reference")
        if set(ids) != set(self.artifact_digests):
            raise ValueError("artifact_digest_ids_mismatch")
        for digest in self.artifact_digests.values():
            _valid_hash(digest)
        return self


class SourceSignature(StrictModel):
    key_id: str = Field(min_length=1, max_length=256)
    signature_hex: str = Field(pattern=r"^[0-9a-f]{128}$")
    signed_at: AwareDatetime
    expires_at: AwareDatetime


class ValidateRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=128)


class RegisterRequest(ValidateRequest):
    evidence: EvidenceEnvelope
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: SourceSignature


class ApprovalRequest(ValidateRequest):
    policy_version: str = Field(min_length=1, max_length=128)
    equality_leak_review: Literal["accepted"]
    privacy_review: Literal["accepted"]


class ActionRequest(ValidateRequest):
    reason: str = Field(min_length=1, max_length=1000)


class SubmissionRequest(ValidateRequest):
    chain: Chain
    adapter: Literal["offline_fixture", "production"]
    receipt_id: str = Field(min_length=1, max_length=256)
    transaction_id: str = Field(min_length=1, max_length=256)
    accepted: bool


class VerificationRequest(ValidateRequest):
    proof: FixtureProof


def _valid_hash(value: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("invalid_sha256")


def canonical_bytes(value: Any) -> bytes:
    """MINDEX canonical JSON v1: NFC, sorted keys, UTF-8, no floats, safe ints.

    This is a named constrained profile, not an assertion of RFC 8785 compliance.
    Reject normalization collisions instead of silently changing evidence.
    """
    def normalize(item: Any, depth: int = 0):
        if depth > 32:
            raise ValueError("canonical_depth_exceeded")
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, int) and abs(item) <= 2**53 - 1:
            return item
        if isinstance(item, str):
            return unicodedata.normalize("NFC", item)
        if isinstance(item, list):
            return [normalize(v, depth + 1) for v in item]
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            output = {}
            for key, val in item.items():
                normalized = unicodedata.normalize("NFC", key)
                if normalized in output:
                    raise ValueError("canonical_key_collision")
                output[normalized] = normalize(val, depth + 1)
            return output
        raise ValueError("unsupported_canonical_value")
    encoded = json.dumps(normalize(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > 65536:
        raise ValueError("canonical_size_exceeded")
    return encoded


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_record_bytes(principal: ProvenancePrincipal, evidence: EvidenceEnvelope,
                           source: SourceSignature) -> bytes:
    return canonical_bytes({"canonicalization": "mindex-canonical-json-v1", "scope": principal.scope(),
                            "evidence": evidence.model_dump(mode="json"),
                            "source": {"key_id": source.key_id, "signed_at": _iso(source.signed_at),
                                       "expires_at": _iso(source.expires_at)}})


def source_signing_message(principal: ProvenancePrincipal, evidence: EvidenceEnvelope,
                           source: SourceSignature) -> bytes:
    return b"MINDEX-PROVENANCE-SOURCE-V1\x00" + canonical_record_bytes(principal, evidence, source)


def _signature_fresh(source: SourceSignature, now: datetime):
    if source.signed_at > now + timedelta(seconds=30):
        raise ProvenanceError("signature_from_future")
    if source.expires_at <= now or source.expires_at <= source.signed_at:
        raise ProvenanceError("signature_expired")
    if source.expires_at - source.signed_at > timedelta(days=1):
        raise ProvenanceError("signature_validity_too_long")


def _scope(principal):
    return and_(*(records.c[key] == value for key, value in principal.scope().items()))


def _operator(principal):
    if "ledger_operator" not in principal.roles:
        raise ProvenanceError("operator_required", 403)


def _fingerprint(kind: str, request) -> str:
    return hashlib.sha256(canonical_bytes({"kind": kind, "request": request.model_dump(mode="json")})).hexdigest()


async def _load(db, principal, record_id, *, lock=False):
    stmt = select(records).where(_scope(principal), records.c.id == str(record_id))
    if lock:
        stmt = stmt.with_for_update()
    row = (await db.execute(stmt)).mappings().first()
    if row is None:
        raise ProvenanceError("record_not_found", 404)
    return dict(row)


def _view(row):
    return {**row, "recorded": True, "onchain_confirmed": False,
            "biological_validity_verified": False, "ownership_verified": False,
            "broadcast_enabled": False,
            "state_label": "recorded" if row["state"] == "registered" else row["state"]}


async def get_record(db, principal, record_id):
    return _view(await _load(db, principal, record_id))


async def list_records(db, principal, *, limit=50, offset=0):
    _pagination(limit, offset)
    rows = (await db.execute(select(records).where(_scope(principal))
                            .order_by(records.c.created_at.desc(), records.c.id)
                            .limit(limit).offset(offset))).mappings().all()
    return [_view(dict(row)) for row in rows]


def _pagination(limit, offset):
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise ProvenanceError("pagination_out_of_bounds")


async def list_events(db, principal, record_id, *, limit=100, offset=0):
    _pagination(limit, offset)
    await _load(db, principal, record_id)
    return [dict(row) for row in (await db.execute(select(events).where(
        events.c.record_id == str(record_id)).order_by(events.c.version)
        .limit(limit).offset(offset))).mappings().all()]


async def list_queue(db, principal, *, limit=50, offset=0):
    _operator(principal)
    _pagination(limit, offset)
    return [dict(row) for row in (await db.execute(select(queue, records.c.state,
        records.c.qualification).join(records, records.c.id == queue.c.record_id)
        .where(_scope(principal)).order_by(queue.c.updated_at, queue.c.record_id)
        .limit(limit).offset(offset))).mappings().all()]


async def _replay(db, row, request, kind):
    event = (await db.execute(select(events).where(events.c.record_id == row["id"],
        events.c.idempotency_key == request.idempotency_key))).mappings().first()
    if event is None:
        return False
    if event["request_hash"] != _fingerprint(kind, request):
        raise ProvenanceError("idempotency_conflict", 409)
    if event["detail"].get("error"):
        raise ProvenanceError(event["detail"]["error"], event["detail"].get("status_code", 422))
    return True


async def _event(db, principal, row, request, kind, state, detail=None, **changes):
    stamp = _iso(_now())
    version = row["version"] + 1
    changed = await db.execute(update(records).where(records.c.id == row["id"],
        records.c.version == row["version"]).values(state=state, version=version,
        updated_at=stamp, **changes))
    if changed.rowcount != 1:
        await db.rollback()
        raise ProvenanceError("concurrent_transition", 409)
    await db.execute(insert(events).values(id=str(uuid.uuid4()), record_id=row["id"],
        version=version, idempotency_key=request.idempotency_key,
        request_hash=_fingerprint(kind, request), kind=kind, from_state=row["state"],
        to_state=state, actor=principal.scope(), detail=detail or {}, created_at=stamp))
    status = {"registered": "awaiting_validation", "validated": "awaiting_approval",
              "approved": "manual_submission_disabled", "submitted": "awaiting_verification",
              "confirmed": "awaiting_finality", "finalized": "complete_fixture",
              "rejected": "rejected", "failed": "failed", "reorg": "reorg_review"}[state]
    await db.execute(update(queue).where(queue.c.record_id == row["id"]).values(
        status=status, updated_at=stamp, attempts=queue.c.attempts + 1,
        last_error=(detail or {}).get("error")))
    await db.commit()
    return await get_record(db, principal, row["id"])


async def _error(db, principal, row, request, kind, code, status_code=422):
    await _event(db, principal, row, request, kind, row["state"],
                 {"error": code, "status_code": status_code})
    raise ProvenanceError(code, status_code)


async def register(db, principal, request: RegisterRequest):
    request_hash = _fingerprint("register", request)
    existing = (await db.execute(select(records).where(_scope(principal),
        records.c.idempotency_key == request.idempotency_key))).mappings().first()
    if existing:
        if existing["request_hash"] != request_hash:
            raise ProvenanceError("idempotency_conflict", 409)
        return _view(dict(existing))
    _signature_fresh(request.source, _now())
    digest = hashlib.sha256(canonical_record_bytes(principal, request.evidence, request.source)).hexdigest()
    if digest != request.content_hash:
        raise ProvenanceError("content_hash_mismatch")
    for parent_id in request.evidence.parent_record_ids:
        parent = await _load(db, principal, parent_id)
        if parent["state"] in ("registered", "rejected", "failed", "reorg"):
            raise ProvenanceError("parent_not_validated", 409)
    stamp = _iso(_now())
    row = dict(id=str(uuid.uuid4()), **principal.scope(), idempotency_key=request.idempotency_key,
               request_hash=request_hash, content_hash=digest,
               evidence=request.evidence.model_dump(mode="json"),
               source=request.source.model_dump(mode="json"), state="registered",
               qualification="local_only", version=1, created_at=stamp, updated_at=stamp)
    try:
        await db.execute(insert(records).values(**row))
        await db.execute(insert(queue).values(record_id=row["id"], status="awaiting_validation",
                                             attempts=0, updated_at=stamp))
        await db.execute(insert(events).values(id=str(uuid.uuid4()), record_id=row["id"], version=1,
            idempotency_key=request.idempotency_key, request_hash=request_hash, kind="register",
            from_state="unregistered", to_state="registered", actor=principal.scope(),
            detail={"canonicalization": "mindex-canonical-json-v1"}, created_at=stamp))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(select(records).where(_scope(principal),
            records.c.idempotency_key == request.idempotency_key))).mappings().first()
        if existing and existing["request_hash"] == request_hash:
            return _view(dict(existing))
        raise ProvenanceError("idempotency_conflict", 409)
    return await get_record(db, principal, row["id"])


async def validate(db, principal, record_id, request: ValidateRequest, *,
                   trusted_key: TrustedSourceKey, artifact_hashes: dict[str, str]):
    row = await _load(db, principal, record_id, lock=True)
    if await _replay(db, row, request, "validate"):
        return _view(row)
    if row["state"] != "registered":
        return await _error(db, principal, row, request, "validate", "invalid_transition", 409)
    source = SourceSignature.model_validate(row["source"])
    evidence = EvidenceEnvelope.model_validate(row["evidence"])
    try:
        now = _now()
        _signature_fresh(source, now)
        if (trusted_key.key_id != source.key_id or trusted_key.revoked
            or trusted_key.purpose != "provenance-source"
            or any(getattr(trusted_key, key) != val for key, val in principal.scope().items())):
            raise ProvenanceError("source_key_scope_or_status_invalid")
        if (trusted_key.valid_from.tzinfo is None or trusted_key.valid_until.tzinfo is None
            or not trusted_key.valid_from <= source.signed_at <= now < trusted_key.valid_until
            or source.expires_at > trusted_key.valid_until):
            raise ProvenanceError("source_key_expired_or_not_yet_valid")
        payload = canonical_record_bytes(principal, evidence, source)
        if hashlib.sha256(payload).hexdigest() != row["content_hash"]:
            raise ProvenanceError("content_hash_mismatch")
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(trusted_key.public_key_hex)).verify(
            bytes.fromhex(source.signature_hex), source_signing_message(principal, evidence, source))
        if artifact_hashes != evidence.artifact_digests:
            raise ProvenanceError("artifact_content_mismatch")
        for parent_id in evidence.parent_record_ids:
            parent = await _load(db, principal, parent_id)
            if parent["state"] in ("registered", "rejected", "failed", "reorg"):
                raise ProvenanceError("parent_not_validated", 409)
    except (InvalidSignature, ValueError):
        return await _error(db, principal, row, request, "validate", "invalid_source_signature")
    except ProvenanceError as exc:
        return await _error(db, principal, row, request, "validate", exc.code, exc.status_code)
    return await _event(db, principal, row, request, "validate", "validated",
        {"source_key_id": source.key_id, "artifact_count": len(artifact_hashes),
         "signature_verified": True, "artifact_hashes_verified": True})


async def approve(db, principal, record_id, request: ApprovalRequest):
    _operator(principal)
    row = await _load(db, principal, record_id, lock=True)
    if await _replay(db, row, request, "approve"):
        return _view(row)
    if row["state"] != "validated":
        return await _error(db, principal, row, request, "approve", "invalid_transition", 409)
    approval = {"actor": principal.scope(), "approved_at": _iso(_now()),
                "policy_version": request.policy_version,
                "privacy_review": request.privacy_review,
                "equality_leak_review": request.equality_leak_review,
                "public_broadcast_authorized": False}
    return await _event(db, principal, row, request, "approve", "approved", approval, approval=approval)


async def _action(db, principal, record_id, request, kind):
    _operator(principal)
    row = await _load(db, principal, record_id, lock=True)
    if await _replay(db, row, request, kind):
        return _view(row)
    allowed = {"reject": {"registered", "validated", "approved", "submitted", "reorg"},
               "fail": {"registered", "validated", "approved", "submitted", "confirmed", "reorg"}}
    if row["state"] not in allowed[kind]:
        return await _error(db, principal, row, request, kind, "invalid_transition", 409)
    state = "rejected" if kind == "reject" else "failed"
    return await _event(db, principal, row, request, kind, state, {"reason": request.reason})


async def reject(db, principal, record_id, request: ActionRequest):
    return await _action(db, principal, record_id, request, "reject")


async def fail(db, principal, record_id, request: ActionRequest):
    return await _action(db, principal, record_id, request, "fail")


async def submit(db, principal, record_id, request: SubmissionRequest):
    _operator(principal)
    row = await _load(db, principal, record_id, lock=True)
    if await _replay(db, row, request, "submit"):
        return _view(row)
    if request.adapter != "offline_fixture":
        return await _error(db, principal, row, request, "submit", "adapter_not_qualified", 503)
    if row["state"] != "approved":
        return await _error(db, principal, row, request, "submit", "invalid_transition", 409)
    if not request.transaction_id.startswith("fixture:") or not request.receipt_id.startswith("fixture:"):
        return await _error(db, principal, row, request, "submit", "fixture_receipt_required")
    duplicate = (await db.execute(select(receipts.c.id).where(receipts.c.chain == request.chain,
        receipts.c.adapter == request.adapter, (receipts.c.receipt_id == request.receipt_id) |
        (receipts.c.transaction_id == request.transaction_id)))).first()
    if duplicate:
        return await _error(db, principal, row, request, "submit", "duplicate_receipt", 409)
    try:
        async with db.begin_nested():
            await db.execute(insert(receipts).values(id=str(uuid.uuid4()), record_id=row["id"],
                chain=request.chain, adapter=request.adapter, receipt_id=request.receipt_id,
                transaction_id=request.transaction_id, accepted=int(request.accepted), created_at=_iso(_now())))
    except IntegrityError:
        return await _error(db, principal, row, request, "submit", "duplicate_receipt", 409)
    return await _event(db, principal, row, request, "submit",
        "submitted" if request.accepted else "rejected",
        {"receipt_id": request.receipt_id, "accepted": request.accepted,
         "public_payload": public_commitment(row["content_hash"])},
        qualification="offline_fixture", chain=request.chain, transaction_id=request.transaction_id)


async def verify(db, principal, record_id, request: VerificationRequest):
    _operator(principal)
    row = await _load(db, principal, record_id, lock=True)
    if await _replay(db, row, request, "verify"):
        return _view(row)
    if row["state"] not in {"submitted", "confirmed", "finalized", "reorg"}:
        return await _error(db, principal, row, request, "verify", "invalid_transition", 409)
    if row["qualification"] != "offline_fixture":
        return await _error(db, principal, row, request, "verify", "adapter_not_qualified", 503)
    previous = row.get("verification") or {}
    try:
        result = verify_fixture(request.proof, commitment=row["content_hash"], chain=row["chain"],
            transaction_id=row["transaction_id"], previous_block_hash=previous.get("block_hash"))
        if previous.get("tip_height", 0) > result["tip_height"]:
            raise ValueError("stale_proof")
        if (previous.get("tip_height") == result["tip_height"]
            and previous.get("tip_hash") != result["tip_hash"]):
            raise ValueError("conflicting_checkpoint")
        if row["state"] == "finalized" and result["state"] == "confirmed":
            raise ValueError("finality_regression")
    except ValueError as exc:
        return await _error(db, principal, row, request, "verify", str(exc))
    # Reorg clears the prior inclusion; recovery requires a new valid proof.
    return await _event(db, principal, row, request, "verify", result["state"], result,
                        verification=result)


async def get_lineage(db, principal, record_id, *, max_nodes=128):
    if not 1 <= max_nodes <= 256:
        raise ProvenanceError("lineage_limit_out_of_bounds")
    pending = [str(record_id)]
    seen = {}
    edges = []
    missing = []
    while pending and len(seen) < max_nodes:
        current = pending.pop()
        if current in seen:
            continue
        try:
            row = await _load(db, principal, current)
        except ProvenanceError:
            if current == str(record_id):
                raise
            missing.append(current)
            continue
        evidence = EvidenceEnvelope.model_validate(row["evidence"])
        source = SourceSignature.model_validate(row["source"])
        matches = hashlib.sha256(canonical_record_bytes(principal, evidence, source)).hexdigest() == row["content_hash"]
        seen[current] = {"id": current, "content_hash": row["content_hash"],
                         "state": row["state"], "canonical_hash_matches": matches}
        for parent in evidence.parent_record_ids:
            edges.append({"child": current, "parent": str(parent)})
            pending.append(str(parent))
    complete = not pending and not missing
    return {"root_record_id": str(record_id), "nodes": list(seen.values()), "edges": edges,
            "complete": complete, "truncated": bool(pending), "missing_count": len(missing),
            "canonical_hashes_match": all(n["canonical_hash_matches"] for n in seen.values()),
            "verification_scope": "local_stored_parent_traversal",
            "full_verified_dag_path": False, "onchain_confirmed": False}
