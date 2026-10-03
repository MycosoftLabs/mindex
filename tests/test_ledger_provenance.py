"""Offline domain and persistence tests. SQLite does not qualify PostgreSQL deployment."""
from __future__ import annotations

import asyncio
import hashlib
import socket
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mindex_api.ledger import provenance as p
from mindex_api.ledger.provenance_proof import make_fixture_proof
from mindex_api.ledger.provenance_store import metadata


OWNER = p.ProvenancePrincipal("https://identity.example", "alice", "tenant-a", "project-a")
OPERATOR = replace(OWNER, roles=frozenset({"ledger_operator"}))
ARTIFACT_ID = "c7e3eaa5-97e3-4454-bef6-f1ae59e04f9e"
ARTIFACT_HASH = hashlib.sha256(b"private retained artifact bytes").hexdigest()
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"offline source key").digest())


def key_for(owner=OWNER):
    now = datetime.now(timezone.utc)
    return p.TrustedSourceKey("fixture-source-1", PRIVATE_KEY.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex(), **owner.scope(),
        valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1))


def request_for(owner=OWNER, *, idempotency_key="registration-001", parents=(),
                signed_at=None, expires_at=None, description="Private fixture evidence"):
    now = datetime.now(timezone.utc)
    evidence = p.EvidenceEnvelope(source_class="synthetic", artifact_ids=[ARTIFACT_ID],
        artifact_digests={ARTIFACT_ID: ARTIFACT_HASH}, parent_record_ids=list(parents),
        description=description, rights=p.RightsMetadata(license_id="private-test-license",
            consent_reference="private-consent-record", association_claim="Evidence only"))
    source = p.SourceSignature(key_id="fixture-source-1", signature_hex="0"*128,
        signed_at=signed_at or now-timedelta(seconds=1),
        expires_at=expires_at or now+timedelta(minutes=10))
    source.signature_hex = PRIVATE_KEY.sign(p.source_signing_message(owner, evidence, source)).hex()
    digest = hashlib.sha256(p.canonical_record_bytes(owner, evidence, source)).hexdigest()
    return p.RegisterRequest(idempotency_key=idempotency_key, evidence=evidence,
                             content_hash=digest, source=source)


@pytest_asyncio.fixture
async def database(tmp_path):
    path = tmp_path / "provenance.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}",
                                execution_options={"schema_translate_map": {"ledger": None}})
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory, path
    await engine.dispose()


async def validated(db, *, owner=OWNER, request=None):
    row = await p.register(db, owner, request or request_for(owner))
    return await p.validate(db, owner, row["id"], p.ValidateRequest(idempotency_key="validate-001"),
                            trusted_key=key_for(owner), artifact_hashes={ARTIFACT_ID: ARTIFACT_HASH})


async def approved(db, *, request=None):
    row = await validated(db, request=request)
    return await p.approve(db, OPERATOR, row["id"], p.ApprovalRequest(idempotency_key="approve-001",
        policy_version="offline-test-policy-v1", privacy_review="accepted", equality_leak_review="accepted"))


async def submitted(db, *, depth=1, request=None, accepted=True):
    row = await approved(db, request=request)
    proof = make_fixture_proof(row["content_hash"], depth=depth)
    row = await p.submit(db, OPERATOR, row["id"], p.SubmissionRequest(idempotency_key="submit-001",
        chain=proof.chain, adapter="offline_fixture", receipt_id="fixture:"+row["id"],
        transaction_id=proof.transaction_id, accepted=accepted))
    return row, proof


def test_canonical_known_bytes_and_rejected_ambiguous_values():
    assert p.canonical_bytes({"z": "e\u0301", "a": [True, None, 17]}) == b'{"a":[true,null,17],"z":"\xc3\xa9"}'
    assert p.canonical_bytes({"z": "é", "a": [True, None, 17]}) == b'{"a":[true,null,17],"z":"\xc3\xa9"}'
    for value in (1.5, float("nan"), 2**53, {"é": 1, "e\u0301": 2}, {1: "bad"}):
        with pytest.raises(ValueError):
            p.canonical_bytes(value)
    with pytest.raises(ValueError, match="size"):
        p.canonical_bytes("x" * 65537)
    with pytest.raises(ValidationError):
        p.SourceSignature(key_id="x", signature_hex="0"*128,
                          signed_at="2026-10-01T00:00:00", expires_at="2026-10-01T00:01:00")


async def test_registration_recorded_distinct_and_exact_replay(database):
    factory, _ = database
    async with factory() as db:
        request = request_for()
        row = await p.register(db, OWNER, request)
        assert row["state"] == "registered" and row["state_label"] == "recorded"
        assert row["transaction_id"] is None and row["qualification"] == "local_only"
        assert not row["onchain_confirmed"] and not row["broadcast_enabled"]
        assert (await p.register(db, OWNER, request))["id"] == row["id"]
        assert len(await p.list_events(db, OWNER, row["id"])) == 1
        changed = request.model_copy(update={"content_hash": "f"*64})
        with pytest.raises(p.ProvenanceError, match="idempotency_conflict"):
            await p.register(db, OWNER, changed)


@pytest.mark.parametrize("change", [dict(subject="bob"), dict(project_id="project-b"),
    dict(tenant_id="tenant-b"), dict(issuer="https://unrelated.example")])
async def test_scope_isolation_every_read_and_write(database, change):
    factory, _ = database
    other = replace(OPERATOR, **change)
    async with factory() as db:
        row = await validated(db)
        assert await p.list_records(db, other) == []
        assert await p.list_queue(db, other) == []
        for operation in [p.get_record(db, other, row["id"]),
                          p.list_events(db, other, row["id"]),
                          p.get_lineage(db, other, row["id"]),
                          p.reject(db, other, row["id"], p.ActionRequest(idempotency_key="reject-other", reason="other"))]:
            with pytest.raises(p.ProvenanceError, match="record_not_found"):
                await operation
        assert (await p.get_record(db, OWNER, row["id"]))["state"] == "validated"


async def test_hash_mismatch_stale_registration_and_key_bound_signature(database):
    factory, _ = database
    async with factory() as db:
        request = request_for()
        with pytest.raises(p.ProvenanceError, match="content_hash_mismatch"):
            await p.register(db, OWNER, request.model_copy(update={"content_hash": "f"*64}))
        expired = request_for(signed_at=datetime.now(timezone.utc)-timedelta(hours=2),
                              expires_at=datetime.now(timezone.utc)-timedelta(hours=1))
        with pytest.raises(p.ProvenanceError, match="signature_expired"):
            await p.register(db, OWNER, expired)
        row = await p.register(db, OWNER, request)
        for suffix, key in [("owner", replace(key_for(), subject="bob")),
                             ("revoked", replace(key_for(), revoked=True)),
                             ("purpose", replace(key_for(), purpose="chain-signing"))]:
            with pytest.raises(p.ProvenanceError, match="source_key_scope_or_status_invalid"):
                await p.validate(db, OWNER, row["id"], p.ValidateRequest(idempotency_key="validate-"+suffix),
                                 trusted_key=key, artifact_hashes={ARTIFACT_ID: ARTIFACT_HASH})
        log = await p.list_events(db, OWNER, row["id"])
        assert len(log) == 4 and log[-1]["detail"]["error"] == "source_key_scope_or_status_invalid"
        assert all(event["to_state"] == "registered" for event in log)


async def test_invalid_signature_artifact_mutation_and_stale_validation_audited(database, monkeypatch):
    factory, _ = database
    async with factory() as db:
        request = request_for()
        request.source.signature_hex = "0"*128
        row = await p.register(db, OWNER, request)
        with pytest.raises(p.ProvenanceError, match="invalid_source_signature"):
            await p.validate(db, OWNER, row["id"], p.ValidateRequest(idempotency_key="validate-bad"),
                             trusted_key=key_for(), artifact_hashes={ARTIFACT_ID: ARTIFACT_HASH})
        good = await p.register(db, OWNER, request_for(idempotency_key="registration-good"))
        with pytest.raises(p.ProvenanceError, match="artifact_content_mismatch"):
            await p.validate(db, OWNER, good["id"], p.ValidateRequest(idempotency_key="validate-artifacts"),
                             trusted_key=key_for(), artifact_hashes={ARTIFACT_ID: "f"*64})
        future = datetime.now(timezone.utc)+timedelta(hours=1)
        monkeypatch.setattr(p, "_now", lambda: future)
        with pytest.raises(p.ProvenanceError, match="signature_expired"):
            await p.validate(db, OWNER, good["id"], p.ValidateRequest(idempotency_key="validate-expired"),
                             trusted_key=key_for(), artifact_hashes={ARTIFACT_ID: ARTIFACT_HASH})
        log = await p.list_events(db, OWNER, good["id"])
        assert [e["detail"].get("error") for e in log] == [None, "artifact_content_mismatch", "signature_expired"]


async def test_operator_approval_preserves_policy_and_direct_skip_denied(database):
    factory, _ = database
    async with factory() as db:
        row = await p.register(db, OWNER, request_for())
        approval = p.ApprovalRequest(idempotency_key="approval-first", policy_version="policy-v1",
                                    privacy_review="accepted", equality_leak_review="accepted")
        with pytest.raises(p.ProvenanceError, match="operator_required"):
            await p.approve(db, OWNER, row["id"], approval)
        with pytest.raises(p.ProvenanceError, match="invalid_transition"):
            await p.approve(db, OPERATOR, row["id"], approval)
        await p.validate(db, OWNER, row["id"], p.ValidateRequest(idempotency_key="validate-first"),
                         trusted_key=key_for(), artifact_hashes={ARTIFACT_ID: ARTIFACT_HASH})
        approval.idempotency_key = "approval-second"
        row = await p.approve(db, OPERATOR, row["id"], approval)
        assert row["approval"]["actor"]["subject"] == "alice"
        assert row["approval"]["approved_at"] and row["approval"]["policy_version"] == "policy-v1"
        assert not row["approval"]["public_broadcast_authorized"]
        assert (await p.list_queue(db, OPERATOR))[0]["status"] == "manual_submission_disabled"


async def test_offline_confirmation_finality_reorg_and_recovery_no_network(database, monkeypatch):
    def no_connect(*args, **kwargs):
        pytest.fail("network connection attempted")
    monkeypatch.setattr(socket.socket, "connect", no_connect)
    factory, _ = database
    async with factory() as db:
        row, proof = await submitted(db)
        assert row["state"] == "submitted" and row["verification"] is None
        confirmed = await p.verify(db, OPERATOR, row["id"],
            p.VerificationRequest(idempotency_key="verify-confirmed", proof=proof))
        assert confirmed["state"] == "confirmed" and not confirmed["onchain_confirmed"]
        final = make_fixture_proof(row["content_hash"], depth=3)
        finalized = await p.verify(db, OPERATOR, row["id"],
            p.VerificationRequest(idempotency_key="verify-finalized", proof=final))
        assert finalized["state"] == "finalized" and finalized["qualification"] == "offline_fixture"
        fork = make_fixture_proof(row["content_hash"], depth=4, fork="reorg", included=False)
        reorg = await p.verify(db, OPERATOR, row["id"],
            p.VerificationRequest(idempotency_key="verify-reorg", proof=fork))
        assert reorg["state"] == "reorg" and reorg["verification"]["confirmations"] == 0
        recovered = make_fixture_proof(row["content_hash"], depth=5, fork="recovered")
        row = await p.verify(db, OPERATOR, row["id"],
            p.VerificationRequest(idempotency_key="verify-recovered", proof=recovered))
        assert row["state"] == "finalized" and not row["onchain_confirmed"]
        log = await p.list_events(db, OWNER, row["id"])
        assert [e["to_state"] for e in log] == ["registered", "validated", "approved", "submitted",
                                                "confirmed", "finalized", "reorg", "finalized"]


async def test_invalid_proof_stale_checkpoint_and_conflicting_checkpoint_audited(database):
    factory, _ = database
    async with factory() as db:
        row, proof = await submitted(db, depth=3)
        await p.verify(db, OPERATOR, row["id"], p.VerificationRequest(idempotency_key="verify-final", proof=proof))
        for name, bad, error in [
            ("stale", make_fixture_proof(row["content_hash"], depth=1), "stale_proof"),
            ("fork", make_fixture_proof(row["content_hash"], depth=3, fork="fork"), "conflicting_checkpoint"),
            ("hash", make_fixture_proof("f"*64, depth=4), "proof_binding_mismatch")]:
            with pytest.raises(p.ProvenanceError, match=error):
                await p.verify(db, OPERATOR, row["id"],
                    p.VerificationRequest(idempotency_key="verify-"+name, proof=bad))
        assert (await p.get_record(db, OWNER, row["id"]))["state"] == "finalized"
        assert (await p.list_events(db, OWNER, row["id"]))[-1]["detail"]["error"] == "proof_binding_mismatch"


async def test_production_disabled_and_receipt_rejection_duplicate_conflict(database):
    factory, _ = database
    async with factory() as db:
        row = await approved(db)
        with pytest.raises(p.ProvenanceError, match="adapter_not_qualified") as exc:
            await p.submit(db, OPERATOR, row["id"], p.SubmissionRequest(idempotency_key="submit-live",
                chain="solana", adapter="production", receipt_id="remote200", transaction_id="signature", accepted=True))
        assert exc.value.status_code == 503
        proof = make_fixture_proof(row["content_hash"])
        request = p.SubmissionRequest(idempotency_key="submit-test", chain=proof.chain,
            adapter="offline_fixture", receipt_id="fixture:receipt-unique", transaction_id=proof.transaction_id, accepted=False)
        row = await p.submit(db, OPERATOR, row["id"], request)
        assert row["state"] == "rejected" and row["verification"] is None
        assert (await p.submit(db, OPERATOR, row["id"], request))["state"] == "rejected"
        with pytest.raises(p.ProvenanceError, match="idempotency_conflict"):
            await p.submit(db, OPERATOR, row["id"], request.model_copy(update={"accepted": True}))
        other = await approved(db, request=request_for(idempotency_key="other-registration"))
        with pytest.raises(p.ProvenanceError, match="duplicate_receipt"):
            await p.submit(db, OPERATOR, other["id"], request)
        assert (await p.get_record(db, OWNER, other["id"]))["state"] == "approved"


@pytest.mark.parametrize("action,expected", [(p.reject, "rejected"), (p.fail, "failed")])
async def test_operator_terminal_actions(database, action, expected):
    factory, _ = database
    async with factory() as db:
        row = await validated(db)
        request = p.ActionRequest(idempotency_key="operator-action", reason="Evidence review did not pass")
        with pytest.raises(p.ProvenanceError, match="operator_required"):
            await action(db, OWNER, row["id"], request)
        row = await action(db, OPERATOR, row["id"], request)
        assert row["state"] == expected
        assert (await p.list_events(db, OWNER, row["id"]))[-1]["detail"]["reason"] == request.reason


async def test_restart_durable_queue_receipts_events_and_replay(database):
    factory, path = database
    request = request_for()
    async with factory() as db:
        row, proof = await submitted(db, request=request)
    # Fresh engine/session against the same durable file, not an in-memory Python list.
    restarted = create_async_engine(f"sqlite+aiosqlite:///{path}",
        execution_options={"schema_translate_map": {"ledger": None}})
    try:
        async with async_sessionmaker(restarted)() as db:
            readback = await p.get_record(db, OWNER, row["id"])
            assert readback["state"] == "submitted"
            assert len(await p.list_events(db, OWNER, row["id"])) == 4
            assert (await p.list_queue(db, OPERATOR))[0]["record_id"] == row["id"]
            assert (await p.register(db, OWNER, request))["id"] == row["id"]
            # Both exact receipt replay and receipt uniqueness survive restart.
            receipt = p.SubmissionRequest(idempotency_key="submit-001", chain=proof.chain,
                adapter="offline_fixture", receipt_id="fixture:"+row["id"],
                transaction_id=proof.transaction_id, accepted=True)
            assert (await p.submit(db, OPERATOR, row["id"], receipt))["state"] == "submitted"
            other = await approved(db, request=request_for(idempotency_key="restart-other"))
            with pytest.raises(p.ProvenanceError, match="duplicate_receipt"):
                await p.submit(db, OPERATOR, other["id"], receipt)
            confirmed = await p.verify(db, OPERATOR, row["id"],
                p.VerificationRequest(idempotency_key="restart-verify", proof=proof))
            assert confirmed["state"] == "confirmed" and not confirmed["onchain_confirmed"]
    finally:
        await restarted.dispose()


async def test_concurrent_registration_replay_one_record_one_event(database):
    factory, _ = database
    request = request_for()
    async def register_independent_session():
        async with factory() as db:
            return await p.register(db, OWNER, request)
    first, second = await asyncio.gather(register_independent_session(), register_independent_session())
    assert first["id"] == second["id"]
    async with factory() as db:
        assert len(await p.list_records(db, OWNER)) == 1
        assert len(await p.list_events(db, OWNER, first["id"])) == 1
        assert len(await p.list_queue(db, OPERATOR)) == 1


async def test_transaction_failure_preserves_no_partial_record(database, monkeypatch):
    factory, _ = database
    async with factory() as db:
        execute = db.execute
        async def broken_execute(statement, *args, **kwargs):
            if getattr(getattr(statement, "table", None), "name", None) == "provenance_event":
                raise asyncio.CancelledError("simulated cancellation before atomic audit insert")
            return await execute(statement, *args, **kwargs)
        monkeypatch.setattr(db, "execute", broken_execute)
        with pytest.raises(asyncio.CancelledError):
            await p.register(db, OWNER, request_for())
    async with factory() as db:
        assert await p.list_records(db, OWNER) == []
        assert await p.list_queue(db, OPERATOR) == []


async def test_lineage_real_parent_walk_bounded_not_full_verified_dag(database):
    factory, _ = database
    async with factory() as db:
        parent = await validated(db)
        child = await validated(db, request=request_for(idempotency_key="registration-child", parents=[parent["id"]]))
        result = await p.get_lineage(db, OWNER, child["id"])
        assert len(result["nodes"]) == 2 and result["edges"] == [{"child": child["id"], "parent": parent["id"]}]
        assert result["complete"] and result["canonical_hashes_match"]
        assert not result["full_verified_dag_path"] and not result["onchain_confirmed"]
        limited = await p.get_lineage(db, OWNER, child["id"], max_nodes=1)
        assert limited["truncated"] and not limited["complete"]
        other = replace(OWNER, project_id="unrelated")
        with pytest.raises(p.ProvenanceError, match="record_not_found"):
            await p.register(db, other, request_for(other, parents=[parent["id"]]))


def test_envelope_rejects_unbound_artifacts_claims_and_extra_fields():
    data = request_for().evidence.model_dump(mode="json")
    for mutation in [{"artifact_ids": [ARTIFACT_ID, ARTIFACT_ID]}, {"artifact_digests": {}},
                     {"private_bytes": "should never be accepted"}, {"artifact_digests": {ARTIFACT_ID: "xyz"}}]:
        with pytest.raises(ValidationError):
            p.EvidenceEnvelope.model_validate({**data, **mutation})
    with pytest.raises(ValidationError):
        p.RightsMetadata(license_id="x", consent_reference="y", ownership_verified=True)
