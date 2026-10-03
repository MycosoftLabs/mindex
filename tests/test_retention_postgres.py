"""Disposable PostgreSQL boundary tests, never SQLite or a shared/live database.

Set RETENTION_TEST_DSN to a loopback PostgreSQL database named retention_fixture_*
and RETENTION_TEST_ALLOW_DISPOSABLE=1. The fixture drops/recreates retention schema.
Archive proofs here are explicitly fixture proofs; S3 qualification is separate.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from mindex_api.retention.contracts import Principal, RetentionConfig, RetentionError, admission_metadata
from mindex_api.retention.repository import RetentionRepository, authorize_asyncpg
from mindex_api.retention.service import purge_one
from test_retention_object_store import FakeS3
from mindex_api.retention.object_store import PrivateObjectStore


MIGRATIONS = [Path(__file__).parents[1] / 'migrations/20261001_shared_retention_v1.sql',
              Path(__file__).parents[1] / 'migrations/20261002_private_orphan_reconciliation.sql',
              Path(__file__).parents[1] / 'migrations/20261003_backfill_preupgrade_orphan_reconciliation.sql']
A = Principal('https://issuer.test/auth/v1', 'user-a', '2c05e220-8d2f-4c86-bf47-8b0cd12a8b23',
              '5b84ee26-f338-4385-bd2a-c4a588be9cd8')
B = Principal(A.issuer, 'user-b', A.tenant_id, A.project_id)
OTHER = Principal(A.issuer, A.subject, '7d76e1a4-e722-4dd4-9089-ae3edb46590e',
                  '39c50ee8-8454-4735-9d78-cd7a411642a1')
CONFIG = RetentionConfig(enabled=True, bucket='fixture-private')


@pytest.mark.asyncio
async def test_asyncpg_shared_authority_requires_caller_transaction(db):
    connection = await asyncpg.connect(guarded_dsn())
    try:
        with pytest.raises(RetentionError) as error:
            await authorize_asyncpg(connection, A)
        assert error.value.code == 'authorization_transaction_required'
        async with connection.transaction():
            assert (await authorize_asyncpg(connection, A))['subject'] == A.subject
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_asyncpg_shared_authority_denies_revoked_scope(db):
    sessions, _ = db
    await sql(sessions, 'UPDATE retention.membership SET active=false WHERE subject=:s', s=A.subject)
    connection = await asyncpg.connect(guarded_dsn())
    try:
        async with connection.transaction():
            with pytest.raises(RetentionError) as error:
                await authorize_asyncpg(connection, A)
            assert error.value.status == 403
    finally:
        await connection.close()


def guarded_dsn():
    raw = os.getenv('RETENTION_TEST_DSN', '')
    if not raw:
        pytest.skip('RETENTION_TEST_DSN unset: real disposable PostgreSQL required')
    parsed = urlsplit(raw.replace('postgresql+asyncpg://', 'postgresql://', 1))
    if (os.getenv('RETENTION_TEST_ALLOW_DISPOSABLE') != '1'
            or parsed.scheme != 'postgresql' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or not parsed.path.startswith('/retention_fixture_') or parsed.query or parsed.fragment):
        pytest.fail('Refusing non-disposable/non-loopback PostgreSQL test target')
    return parsed.geturl()


@pytest_asyncio.fixture
async def db():
    dsn = guarded_dsn()
    conn = await asyncpg.connect(dsn)
    await conn.execute('DROP SCHEMA IF EXISTS retention CASCADE')
    for migration in MIGRATIONS:
        await conn.execute(migration.read_text(encoding='utf-8'))
    await conn.close()
    engine = create_async_engine(dsn.replace('postgresql://', 'postgresql+asyncpg://', 1),
                                 pool_size=12, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        for p in (A, B, OTHER):
            await session.execute(text("""INSERT INTO retention.membership
                (issuer,subject,tenant_id,project_id,active) VALUES (:i,:s,:t,:p,true)"""),
                {'i': p.issuer, 's': p.subject, 't': p.tenant_id, 'p': p.project_id})
    yield sessions, RetentionRepository(sessions, CONFIG)
    await engine.dispose()


def meta(key='one', **changes):
    return admission_metadata(changes.get('kind', 'dataset'), key,
                              changes.get('media_type', 'application/json'),
                              changes.get('source_event_at', '2026-09-20T08:00:00Z'), CONFIG)


def proof(row, **changes):
    key = '/'.join((CONFIG.prefix, row['tenant_id'], row['project_id'], row['artifact_id']))
    return {'bucket': 'fixture-private', 'key': key, 'version': 'fixture-version-1',
            'verified': True, 'sha256': row['sha256'], 'byte_length': row['byte_length'], **changes}


async def sql(sessions, query, **params):
    async with sessions() as session, session.begin():
        result = await session.execute(text(query), params)
        return result.mappings().all() if result.returns_rows else []


async def verified(repo):
    result, _ = await repo.admit(A, meta(), b'{"fixture":true}')
    row = await repo.claim()
    assert await repo.complete(row, proof(row))
    return result, await repo.get(A, result['artifact_id'])


@pytest.mark.asyncio
async def test_admission_is_one_atomic_durable_record(db):
    sessions, repo = db
    rec, created = await repo.admit(A, meta(), b'{}')
    assert created and rec['state'] == 'pending' and rec['available_at'] is None
    assert rec['source_event_at'] < rec['received_at']
    assert not rec['archive_verified'] and rec['coverage_watermark'] is None
    assert not {'payload', 'object_key', 'lease_token', 'owner_subject'} & rec.keys()
    row = await RetentionRepository(sessions, CONFIG).get(A, rec['artifact_id'])
    assert bytes(row['payload']) == b'{}'
    counts = await sql(sessions, """SELECT (SELECT count(*) FROM retention.artifact) AS artifacts,
        (SELECT count(*) FROM retention.job) AS jobs,(SELECT count(*) FROM retention.outbox) AS events""")
    assert tuple(counts[0].values()) == (1, 1, 1)


@pytest.mark.asyncio
async def test_concurrent_replay_admits_once_and_changed_content_conflicts(db):
    sessions, repo = db
    attempts = await asyncio.gather(*(repo.admit(A, meta(), b'{}') for _ in range(10)))
    assert sum(created for _, created in attempts) == 1
    assert len({rec['artifact_id'] for rec, _ in attempts}) == 1
    for metadata, payload in ((meta(), b'[]'), (meta(media_type='text/plain'), b'{}')):
        with pytest.raises(RetentionError) as exc:
            await repo.admit(A, metadata, payload)
        assert exc.value.status == 409


@pytest.mark.asyncio
async def test_cross_user_scope_guess_search_cancel_delete_denied(db):
    _, repo = db
    rec, _ = await repo.admit(A, meta(), b'{}')
    for actor in (B, OTHER):
        assert await repo.list(actor) == []
        assert await repo.list(actor, query=rec['artifact_id']) == []
        for fn, identifier in ((repo.get, rec['artifact_id']), (repo.get_job, rec['job_id']),
                               (repo.cancel, rec['job_id']), (repo.delete, rec['artifact_id'])):
            with pytest.raises(RetentionError) as exc:
                await fn(actor, identifier)
            assert exc.value.status == 404


@pytest.mark.asyncio
async def test_missing_or_revoked_membership_denies_every_operation(db):
    sessions, repo = db
    rec, row = await verified(repo)
    memory = await repo.memory_link(A, rec['artifact_id'], 'Private fixture', proof_sha256=row['sha256'])
    await sql(sessions, 'UPDATE retention.membership SET active=false WHERE subject=:subject', subject=A.subject)
    calls = [repo.require_membership(A), repo.get(A, rec['artifact_id']), repo.list(A),
             repo.admit(A, meta('two'), b'{}'), repo.cancel(A, rec['job_id']),
             repo.delete(A, rec['artifact_id']), repo.get_job(A, rec['job_id']),
             repo.memory_get(A, memory['memory_id']),
             repo.memory_link(A, rec['artifact_id'], 'Private', proof_sha256=row['sha256'])]
    results = await asyncio.gather(*calls, return_exceptions=True)
    assert all(isinstance(err, RetentionError) and err.status == 403 for err in results)


@pytest.mark.asyncio
async def test_explicit_read_grant_requires_membership_and_does_not_allow_mutation(db):
    sessions, repo = db
    rec, row = await verified(repo)
    await sql(sessions, """INSERT INTO retention.access_grant
        (artifact_id,grantee_issuer,grantee_subject,active) VALUES (:a,:i,:s,true)""",
        a=rec['artifact_id'], i=B.issuer, s=B.subject)
    assert (await repo.get(B, rec['artifact_id']))['sha256'] == row['sha256']
    memory = await repo.memory_link(B, rec['artifact_id'], 'Shared fixture', proof_sha256=row['sha256'])
    with pytest.raises(RetentionError):
        await repo.delete(B, rec['artifact_id'])
    await sql(sessions, 'UPDATE retention.access_grant SET active=false')
    for fn, key in ((repo.get, rec['artifact_id']), (repo.memory_get, memory['memory_id'])):
        with pytest.raises(RetentionError) as exc:
            await fn(B, key)
        assert exc.value.status == 404


@pytest.mark.asyncio
async def test_failed_outbox_insert_rolls_back_job_and_payload(db):
    sessions, repo = db
    await sql(sessions, """CREATE FUNCTION retention.fixture_fail() RETURNS trigger LANGUAGE plpgsql AS
        $$ BEGIN RAISE EXCEPTION 'fixture admission crash'; END $$""")
    await sql(sessions, """CREATE TRIGGER fixture_fail BEFORE INSERT ON retention.outbox
        FOR EACH ROW EXECUTE FUNCTION retention.fixture_fail()""")
    with pytest.raises(Exception, match='fixture admission crash'):
        await repo.admit(A, meta(), b'{}')
    for table in ('artifact', 'job', 'outbox'):
        assert (await sql(sessions, f'SELECT count(*) AS n FROM retention.{table}'))[0]['n'] == 0


@pytest.mark.asyncio
async def test_crash_after_commit_is_claimable_from_new_repository(db):
    sessions, repo = db
    rec, _ = await repo.admit(A, meta(), b'{"recoverable":true}')
    restarted = RetentionRepository(sessions, CONFIG)
    row = await restarted.claim()
    assert row['artifact_id'] == rec['artifact_id'] and bytes(row['payload']) == b'{"recoverable":true}'
    assert await restarted.complete(row, proof(row))
    assert (await repo.get_job(A, rec['job_id']))['archive_verified']


@pytest.mark.asyncio
async def test_stale_worker_fenced_after_lease_reclaim(db):
    sessions, repo = db
    await repo.admit(A, meta(), b'{}')
    stale = await repo.claim()
    assert await repo.claim() is None
    await sql(sessions, "UPDATE retention.job SET lease_expires_at=now()-interval '1 second'")
    fresh = await repo.claim()
    assert fresh['lease_token'] != stale['lease_token'] and fresh['attempt_count'] == 2
    assert not await repo.complete(stale, proof(stale))
    assert not await repo.retry(stale, 'archive_unavailable')
    assert await repo.complete(fresh, proof(fresh))


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'sha256': '0' * 64}, {'byte_length': 999}, {'verified': False}, {'version': ''}])
async def test_invalid_object_proof_quarantines_without_watermark(db, changes):
    sessions, repo = db
    rec, _ = await repo.admit(A, meta(), b'{}')
    row = await repo.claim()
    assert not await repo.complete(row, proof(row, **changes))
    actual = await repo.get(A, rec['artifact_id'])
    assert actual['state'] == 'quarantined' and actual['available_at'] is None
    assert bytes(actual['payload']) == b'{}'
    assert await repo.claim() is None
    assert (await sql(sessions, 'SELECT state FROM retention.outbox'))[0]['state'] == 'quarantined'


@pytest.mark.asyncio
async def test_retry_is_bounded_and_scrubs_provider_errors(db):
    sessions, repo = db
    rec, _ = await repo.admit(A, meta(), b'{}')
    for attempt in range(8):
        row = await repo.claim()
        assert row is not None
        assert await repo.retry(row, 'SECRET arbitrary provider exception')
        await sql(sessions, "UPDATE retention.job SET next_attempt_at=now()-interval '1 second'")
    row = await repo.get(A, rec['artifact_id'])
    assert row['state'] == 'quarantined' and row['last_error_code'] == 'archive_failed'
    assert await repo.claim() is None


@pytest.mark.asyncio
async def test_concurrent_quota_is_project_wide_and_replay_still_succeeds(db):
    sessions, _ = db
    repo = RetentionRepository(sessions, replace(CONFIG, max_payload_bytes=2, max_pending_count=1, max_pending_bytes=2))
    attempts = await asyncio.gather(repo.admit(A, meta('a'), b'{}'), repo.admit(B, meta('b'), b'{}'),
                                    return_exceptions=True)
    assert sum(isinstance(x, RetentionError) and x.status == 429 for x in attempts) == 1
    winner = A if isinstance(attempts[0], tuple) else B
    key = 'a' if winner == A else 'b'
    assert not (await repo.admit(winner, meta(key), b'{}'))[1]


@pytest.mark.asyncio
async def test_cancel_wipes_payload_and_fences_active_worker(db):
    sessions, repo = db
    rec, _ = await repo.admit(A, meta(), b'{}')
    leased = await repo.claim()
    result = await repo.cancel(A, rec['job_id'])
    assert result['state'] == 'cancelled'
    assert not await repo.complete(leased, proof(leased))
    row = (await sql(sessions, 'SELECT payload,state FROM retention.artifact'))[0]
    assert row['payload'] is None and row['state'] == 'cancelled'
    assert await repo.list(A) == []


@pytest.mark.asyncio
async def test_delete_revokes_memory_and_defers_physical_cleanup_until_retention(db):
    sessions, repo = db
    rec, row = await verified(repo)
    memory = await repo.memory_link(A, rec['artifact_id'], 'fixture summary', proof_sha256=row['sha256'])
    assert (await repo.memory_get(A, memory['memory_id']))['state'] == 'referenced'
    deleted = await repo.delete(A, rec['artifact_id'])
    assert deleted['state'] == 'deleted' and deleted['physical_deletion_pending']
    assert deleted['available_at'] is None and not deleted['archive_verified']
    with pytest.raises(RetentionError):
        await repo.memory_get(A, memory['memory_id'])
    assert (await sql(sessions, 'SELECT summary FROM retention.memory_reference'))[0]['summary'] == ''
    assert await repo.claim_purge() is None
    await sql(sessions, "UPDATE retention.artifact SET retention_until=now()-interval '1 second'")
    purge = await repo.claim_purge()
    assert purge['object_version'] == 'fixture-version-1'
    deleted_proof = {'deleted': True, 'bucket': purge['object_bucket'], 'key': purge['object_key'],
                     'version': purge['object_version']}
    assert not await repo.complete_purge(purge, dict(deleted_proof, version='wrong'))
    assert not await repo.complete_purge(dict(purge, purge_lease_token='stale'), deleted_proof)
    assert await repo.complete_purge(purge, deleted_proof)
    assert (await sql(sessions, 'SELECT physical_deleted_at FROM retention.artifact'))[0]['physical_deleted_at']


@pytest.mark.asyncio
async def test_crash_after_archive_commit_reconciles_only_tombstoned_tenant_key(db):
    sessions, repo = db
    object_config = replace(CONFIG, prefix='private-retention-v1', expected_owner='123456789012',
                            region='us-west-2',
                            kms_key='arn:aws:kms:us-west-2:123456789012:key/test-key')
    client = FakeS3()
    store = PrivateObjectStore(client, object_config)

    rec_a, _ = await repo.admit(A, meta('crashed-a'), b'{}')
    uploaded_a = await repo.claim()
    reference_a = store.archive(uploaded_a)
    assert reference_a['verified'] is True
    # Simulate hard process death here: deliberately skip complete/register_orphan.
    assert (await repo.cancel(A, rec_a['job_id']))['physical_deletion_pending']

    rec_missing, _ = await repo.admit(B, meta('missing-object'), b'{}')
    await repo.cancel(B, rec_missing['job_id'])

    rec_other, _ = await repo.admit(OTHER, meta('other-tenant'), b'{}')
    uploaded_other = await repo.claim()
    reference_other = store.archive(uploaded_other)
    await repo.cancel(OTHER, rec_other['job_id'])

    await sql(sessions, "UPDATE retention.artifact SET retention_until=now()-interval '2 seconds'")
    expired_rows = await sql(sessions, """SELECT artifact_id,retention_until FROM retention.artifact
        WHERE artifact_id IN (:a,:b)""", a=rec_a['artifact_id'], b=rec_other['artifact_id'])
    expired_by_id = {row['artifact_id']: row['retention_until'] for row in expired_rows}
    client.objects[(reference_a['key'], reference_a['version'])]['ObjectLockRetainUntilDate'] = (
        expired_by_id[rec_a['artifact_id']])
    client.objects[(reference_other['key'], reference_other['version'])]['ObjectLockRetainUntilDate'] = (
        expired_by_id[rec_other['artifact_id']])
    client.calls.clear()

    assert await purge_one(repo, store)
    assert (reference_a['key'], reference_a['version']) not in client.objects
    assert (reference_other['key'], reference_other['version']) in client.objects
    object_calls = [(op, args) for op, args in client.calls
                    if op in {'head_object', 'get_object', 'delete_object'}]
    assert object_calls and all(args['Key'] == reference_a['key'] for _, args in object_calls)
    assert all(args.get('VersionId') != reference_other['version'] for _, args in object_calls)
    assert all(args['Prefix'] == reference_a['key'] for op, args in client.calls
               if op == 'list_object_versions')

    # The next tombstone never reached S3. Reconciliation records confirmed
    # absence and does not mislabel that as a physical delete.
    client.calls.clear()
    assert await purge_one(repo, store)
    absent_calls = [(op, args) for op, args in client.calls
                    if op in {'head_object', 'get_object', 'delete_object'}]
    absent_list_calls = [(op, args) for op, args in client.calls
                         if op == 'list_object_versions']
    missing_key = f"{object_config.prefix}/{B.tenant_id}/{B.project_id}/"
    assert not absent_calls
    assert len(absent_list_calls) == 1 and absent_list_calls[0][1]['Prefix'].startswith(missing_key)
    assert not any(op in {'get_object', 'delete_object'} for op, _ in absent_calls)
    assert (reference_other['key'], reference_other['version']) in client.objects
    rows = await sql(sessions, """SELECT a.tenant_id,a.object_version,a.physical_deleted_at,
        a.archive_reconciled_at,o.state AS purge_state FROM retention.artifact a
        JOIN retention.outbox o USING (artifact_id) WHERE o.event='purge_object'
        ORDER BY a.received_at""")
    assert rows[0]['tenant_id'] == A.tenant_id and rows[0]['object_version'] is None
    assert rows[0]['physical_deleted_at'] and rows[0]['archive_reconciled_at']
    assert rows[0]['purge_state'] == 'done'
    assert rows[1]['tenant_id'] == B.tenant_id and rows[1]['physical_deleted_at'] is None
    assert rows[1]['archive_reconciled_at'] and rows[1]['purge_state'] == 'done'
    assert rows[2]['tenant_id'] == OTHER.tenant_id and rows[2]['physical_deleted_at'] is None
    assert rows[2]['archive_reconciled_at'] is None and rows[2]['purge_state'] == 'pending'


@pytest.mark.asyncio
async def test_upgrade_backfills_preexisting_terminal_rows_once_and_recovers_both_keys(db):
    sessions, _ = db
    connection = await asyncpg.connect(guarded_dsn())
    await connection.execute('DROP SCHEMA IF EXISTS retention CASCADE')
    await connection.execute(MIGRATIONS[0].read_text(encoding='utf-8'))
    for principal in (A, B, OTHER):
        await connection.execute("""INSERT INTO retention.membership
            (issuer,subject,tenant_id,project_id,active) VALUES($1,$2,$3,$4,true)""",
            principal.issuer, principal.subject, principal.tenant_id, principal.project_id)
    await connection.close()

    repo = RetentionRepository(sessions, CONFIG)
    object_config = replace(CONFIG, prefix='private-retention-v1', expected_owner='123456789012',
                            region='us-west-2', kms_key='arn:aws:kms:us-west-2:123456789012:key/test-key')
    client = FakeS3()
    store = PrivateObjectStore(client, object_config)
    rec_object, _ = await repo.admit(A, meta('legacy-uploaded'), b'{}')
    uploading = await repo.claim()
    reference = store.archive(uploading)
    rec_absent, _ = await repo.admit(OTHER, meta('legacy-absent'), b'{}')
    rec_live, _ = await repo.admit(B, meta('still-live'), b'{}')

    # Emulate terminal rows from the pre-reconciliation application: archive
    # outboxes were cancelled, but no purge_object row was created without a DB ref.
    await sql(sessions, """UPDATE retention.artifact SET state='cancelled',payload=NULL,
        deletion_requested_at=now() WHERE artifact_id IN (:uploaded,:absent)""",
        uploaded=rec_object['artifact_id'], absent=rec_absent['artifact_id'])
    await sql(sessions, """UPDATE retention.job SET state='cancelled',lease_token=NULL,
        lease_expires_at=NULL WHERE artifact_id IN (:uploaded,:absent)""",
        uploaded=rec_object['artifact_id'], absent=rec_absent['artifact_id'])
    await sql(sessions, """UPDATE retention.outbox SET state='cancelled',completed_at=now(),
        lease_token=NULL,lease_expires_at=NULL WHERE event='archive'
        AND artifact_id IN (:uploaded,:absent)""",
        uploaded=rec_object['artifact_id'], absent=rec_absent['artifact_id'])
    assert (await sql(sessions, """SELECT count(*) AS n FROM retention.outbox
        WHERE event='purge_object'"""))[0]['n'] == 0
    await sql(sessions, """INSERT INTO retention.outbox
        (artifact_id,job_id,event,state,lease_token,lease_expires_at)
        VALUES (:artifact,:job,'purge_object','leased','preupgrade-live-lease',
            now()+interval '1 hour')""", artifact=rec_absent['artifact_id'], job=rec_absent['job_id'])

    connection = await asyncpg.connect(guarded_dsn())
    try:
        for migration in MIGRATIONS[1:]:
            await connection.execute(migration.read_text(encoding='utf-8'))
        await connection.execute(MIGRATIONS[2].read_text(encoding='utf-8'))
    finally:
        await connection.close()
    queued = await sql(sessions, """SELECT artifact_id,state,lease_token,completed_at
        FROM retention.outbox WHERE event='purge_object' ORDER BY artifact_id""")
    assert {r['artifact_id'] for r in queued} == {rec_object['artifact_id'], rec_absent['artifact_id']}
    queued_by_id = {r['artifact_id']: r for r in queued}
    assert queued_by_id[rec_object['artifact_id']]['state'] == 'pending'
    assert queued_by_id[rec_object['artifact_id']]['lease_token'] is None
    assert queued_by_id[rec_absent['artifact_id']]['state'] == 'leased'
    assert queued_by_id[rec_absent['artifact_id']]['lease_token'] == 'preupgrade-live-lease'
    assert all(r['completed_at'] is None for r in queued)
    assert (await sql(sessions, """SELECT count(*) AS n FROM retention.outbox
        WHERE event='purge_object'"""))[0]['n'] == 2
    before_cleanup = await sql(sessions, """SELECT archive_reconciled_at,physical_deleted_at
        FROM retention.artifact WHERE artifact_id IN (:uploaded,:absent)""",
        uploaded=rec_object['artifact_id'], absent=rec_absent['artifact_id'])
    assert all(r['archive_reconciled_at'] is None and r['physical_deleted_at'] is None
               for r in before_cleanup)

    await sql(sessions, """UPDATE retention.artifact SET retention_until=now()-interval '2 seconds'
        WHERE artifact_id IN (:uploaded,:absent)""",
        uploaded=rec_object['artifact_id'], absent=rec_absent['artifact_id'])
    await sql(sessions, """UPDATE retention.outbox SET lease_expires_at=now()-interval '1 second'
        WHERE artifact_id=:id AND event='purge_object'""", id=rec_absent['artifact_id'])
    retention_row = await sql(sessions, "SELECT retention_until FROM retention.artifact WHERE artifact_id=:id",
                              id=rec_object['artifact_id'])
    client.objects[(reference['key'], reference['version'])]['ObjectLockRetainUntilDate'] = (
        retention_row[0]['retention_until'])
    client.calls.clear()
    assert await purge_one(repo, store)
    assert await purge_one(repo, store)
    assert (reference['key'], reference['version']) not in client.objects
    assert (await sql(sessions, """SELECT count(*) AS n FROM retention.outbox
        WHERE event='purge_object' AND state='done'"""))[0]['n'] == 2
    assert (await sql(sessions, """SELECT count(*) AS n FROM retention.artifact
        WHERE artifact_id=:id AND physical_deleted_at IS NOT NULL
        AND archive_reconciled_at IS NOT NULL""", id=rec_object['artifact_id']))[0]['n'] == 1
    assert (await sql(sessions, """SELECT count(*) AS n FROM retention.artifact
        WHERE artifact_id=:id AND physical_deleted_at IS NULL
        AND archive_reconciled_at IS NOT NULL""", id=rec_absent['artifact_id']))[0]['n'] == 1
    live = await sql(sessions, """SELECT state FROM retention.artifact WHERE artifact_id=:id""",
                     id=rec_live['artifact_id'])
    assert live[0]['state'] == 'pending'


@pytest.mark.asyncio
async def test_versionless_purge_retries_fences_stale_and_rejects_forged_proofs(db):
    sessions, repo = db
    object_config = replace(CONFIG, prefix='private-retention-v1', expected_owner='123456789012',
                            region='us-west-2', kms_key='arn:aws:kms:us-west-2:123456789012:key/test-key')
    client = FakeS3()
    store = PrivateObjectStore(client, object_config)
    rec, _ = await repo.admit(A, meta('versionless-fence'), b'{}')
    archive_row = await repo.claim()
    reference = store.archive(archive_row)
    await repo.cancel(A, rec['job_id'])
    await sql(sessions, """UPDATE retention.artifact SET retention_until=now()-interval '2 seconds'
        WHERE artifact_id=:id""", id=rec['artifact_id'])
    retention_row = await sql(sessions, "SELECT retention_until FROM retention.artifact WHERE artifact_id=:id",
                              id=rec['artifact_id'])
    client.objects[(reference['key'], reference['version'])]['ObjectLockRetainUntilDate'] = (
        retention_row[0]['retention_until'])

    # Two workers die after claiming but before the object-store step.
    for _ in range(2):
        crashed = await repo.claim_purge()
        assert crashed and crashed['object_version'] is None
        await sql(sessions, """UPDATE retention.outbox SET lease_expires_at=now()-interval '1 second'
            WHERE artifact_id=:id AND event='purge_object'""", id=rec['artifact_id'])

    purge = await repo.claim_purge()
    assert purge and purge['object_version'] is None
    key = '/'.join((object_config.prefix, A.tenant_id, A.project_id, rec['artifact_id']))
    valid_shape = {'deleted': True, 'reconciled': True, 'verified': True, 'absent': False,
                   'bucket': object_config.bucket, 'key': key, 'version': reference['version'],
                   'sha256': purge['sha256'], 'byte_length': purge['byte_length'],
                   'versions_deleted': 1}
    forged = [dict(valid_shape, bucket='other-private-bucket'),
              dict(valid_shape, key=f"{object_config.prefix}/{OTHER.tenant_id}/{OTHER.project_id}/"
                   f"{rec['artifact_id']}"),
              dict(valid_shape, sha256='0' * 64),
              dict(valid_shape, byte_length=purge['byte_length'] + 1),
              dict(valid_shape, version='null'),
              dict(valid_shape, verified=False),
              dict(valid_shape, versions_deleted=0),
              {key: value for key, value in valid_shape.items() if key != 'versions_deleted'}]
    for proof_shape in forged:
        assert not await repo.complete_purge(purge, proof_shape)
    assert (await sql(sessions, """SELECT state FROM retention.outbox
        WHERE artifact_id=:id AND event='purge_object'""", id=rec['artifact_id']))[0]['state'] == 'leased'

    # The external delete succeeds, then the process dies before DB completion.
    deleted_proof = store.reconcile_delete(purge)
    assert (reference['key'], reference['version']) not in client.objects
    await sql(sessions, """UPDATE retention.outbox SET lease_expires_at=now()-interval '1 second'
        WHERE artifact_id=:id AND event='purge_object'""", id=rec['artifact_id'])
    assert not await repo.complete_purge(purge, deleted_proof)

    recovered = await repo.claim_purge()
    assert recovered and recovered['purge_lease_token'] != purge['purge_lease_token']
    absent_proof = store.reconcile_delete(recovered)
    assert absent_proof['absent'] is True and absent_proof['versions_deleted'] == 0
    assert await repo.complete_purge(recovered, absent_proof)
    final = (await sql(sessions, """SELECT a.archive_reconciled_at,a.physical_deleted_at,
        o.state,o.attempt_count FROM retention.artifact a JOIN retention.outbox o USING (artifact_id)
        WHERE a.artifact_id=:id AND o.event='purge_object'""", id=rec['artifact_id']))[0]
    assert final['archive_reconciled_at'] and final['physical_deleted_at'] is None
    assert final['state'] == 'done' and final['attempt_count'] == 4


@pytest.mark.asyncio
async def test_restored_expired_orphan_and_purge_leases_recover_exact_key(db):
    sessions, repo = db
    dsn = urlsplit(guarded_dsn())
    tools_dir = Path(__file__).parents[2] / 'runtime/postgres/pgsql/bin'
    tools = {name: tools_dir / f'{name}.exe' for name in ('pg_dump', 'pg_restore', 'createdb', 'dropdb')}
    if any(not path.is_file() for path in tools.values()):
        pytest.skip('task-owned portable PostgreSQL dump/restore tools unavailable')
    database = f'retention_fixture_restore_{uuid4().hex[:10]}'
    source_database = dsn.path.lstrip('/')
    host, port, user = dsn.hostname or '127.0.0.1', dsn.port or 5432, unquote(dsn.username or 'retention_fixture')
    dump_args = ['-h', host, '-p', str(port), '-U', user]
    target_dsn = f'postgresql://{user}@{host}:{port}/{database}'
    object_config = replace(CONFIG, prefix='private-retention-v1', expected_owner='123456789012',
                            region='us-west-2', kms_key='arn:aws:kms:us-west-2:123456789012:key/test-key')
    client = FakeS3()
    store = PrivateObjectStore(client, object_config)
    rec, _ = await repo.admit(A, meta('restore-orphan'), b'{}')
    archive_row = await repo.claim()
    reference = store.archive(archive_row)
    await repo.cancel(A, rec['job_id'])
    assert not await repo.complete(archive_row, reference)
    assert await repo.register_orphan(archive_row, reference)
    await sql(sessions, """UPDATE retention.artifact SET retention_until=now()-interval '2 seconds'
        WHERE artifact_id=:id""", id=rec['artifact_id'])
    retention_row = await sql(sessions, "SELECT retention_until FROM retention.artifact WHERE artifact_id=:id",
                              id=rec['artifact_id'])
    client.objects[(reference['key'], reference['version'])]['ObjectLockRetainUntilDate'] = (
        retention_row[0]['retention_until'])
    purge_lease = await repo.claim_purge()
    orphan_lease = await repo.claim_orphan_purge()
    assert purge_lease and orphan_lease
    assert orphan_lease['object_version'] == reference['version']
    old_purge_token, old_orphan_token = purge_lease['purge_lease_token'], orphan_lease['orphan_lease_token']
    await sql(sessions, """UPDATE retention.outbox SET lease_expires_at=now()-interval '1 second'
        WHERE artifact_id=:id AND event='purge_object'""", id=rec['artifact_id'])
    await sql(sessions, """UPDATE retention.orphan_archive SET lease_expires_at=now()-interval '1 second'
        WHERE artifact_id=:id""", id=rec['artifact_id'])
    with tempfile.TemporaryDirectory(prefix='brief09-restore-') as workdir:
        dump_path = Path(workdir) / 'retention.dump'
        created = False
        try:
            catalog_dsn = f'postgresql://{user}@{host}:{port}/postgres'
            catalog = await asyncpg.connect(catalog_dsn)
            try:
                exists = await catalog.fetchval('SELECT 1 FROM pg_database WHERE datname=$1', database)
                assert exists is None
            finally:
                await catalog.close()
            result = subprocess.run([str(tools['createdb']), *dump_args, database],
                                    capture_output=True, text=True, check=False)
            assert result.returncode == 0, result.stderr
            created = True
            result = subprocess.run([str(tools['pg_dump']), *dump_args, '-d', source_database,
                                     '--schema=retention', '--format=custom', '--file', str(dump_path)],
                                    capture_output=True, text=True, check=False)
            assert result.returncode == 0, result.stderr
            result = subprocess.run([str(tools['pg_restore']), *dump_args, '--dbname', target_dsn,
                                     '--exit-on-error', '--no-owner', '--no-privileges', str(dump_path)],
                                    capture_output=True, text=True, check=False)
            assert result.returncode == 0, result.stderr

            restored_engine = create_async_engine(target_dsn.replace(
                'postgresql://', 'postgresql+asyncpg://', 1))
            try:
                restored_sessions = async_sessionmaker(restored_engine, expire_on_commit=False)
                restored_repo = RetentionRepository(restored_sessions, CONFIG)
                restored_state = (await sql(restored_sessions, """SELECT a.object_version,
                    a.archive_reconciled_at,a.physical_deleted_at,o.state,o.lease_token,
                    o.attempt_count,oa.state AS orphan_state,oa.lease_token AS orphan_token
                    FROM retention.artifact a JOIN retention.outbox o USING (artifact_id)
                    JOIN retention.orphan_archive oa USING (artifact_id)
                    WHERE a.artifact_id=:id AND o.event='purge_object'""", id=rec['artifact_id']))[0]
                assert restored_state['object_version'] is None
                assert restored_state['archive_reconciled_at'] is None
                assert restored_state['physical_deleted_at'] is None
                assert restored_state['state'] == 'leased' and restored_state['lease_token'] == old_purge_token
                assert restored_state['orphan_state'] == 'leased'
                assert restored_state['orphan_token'] == old_orphan_token

                recovered_purge = await restored_repo.claim_purge()
                recovered_orphan = await restored_repo.claim_orphan_purge()
                assert recovered_purge and recovered_orphan
                assert recovered_purge['purge_lease_token'] != old_purge_token
                assert recovered_orphan['orphan_lease_token'] != old_orphan_token
                purge_proof = store.reconcile_delete(recovered_purge)
                assert await restored_repo.complete_purge(recovered_purge, purge_proof)
                orphan_proof = store.delete(recovered_orphan)
                assert await restored_repo.complete_orphan_purge(recovered_orphan, orphan_proof)
                final = (await sql(restored_sessions, """SELECT a.archive_reconciled_at,
                    a.physical_deleted_at,o.state AS purge_state,oa.state AS orphan_state
                    FROM retention.artifact a JOIN retention.outbox o USING (artifact_id)
                    JOIN retention.orphan_archive oa USING (artifact_id)
                    WHERE a.artifact_id=:id AND o.event='purge_object'""", id=rec['artifact_id']))[0]
                assert final['archive_reconciled_at'] and final['physical_deleted_at']
                assert final['purge_state'] == 'done' and final['orphan_state'] == 'done'
                assert (reference['key'], reference['version']) not in client.objects
            finally:
                await restored_engine.dispose()
        finally:
            if created:
                result = subprocess.run([str(tools['dropdb']), *dump_args, database],
                                        capture_output=True, text=True, check=False)
                assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_expiry_denies_reads_then_sweeps_payload_and_memory(db):
    sessions, repo = db
    rec, _ = await repo.admit(A, meta(), b'{}')
    await sql(sessions, "UPDATE retention.artifact SET retention_until=now()-interval '1 second'")
    with pytest.raises(RetentionError):
        await repo.get(A, rec['artifact_id'])
    assert await repo.list(A) == [] and await repo.claim() is None
    assert await repo.expire() == 1
    row = (await sql(sessions, 'SELECT payload,state FROM retention.artifact'))[0]
    assert row['payload'] is None and row['state'] == 'deleted'


@pytest.mark.asyncio
async def test_memory_requires_exact_verified_proof_and_never_implies_learning(db):
    _, repo = db
    rec, _ = await repo.admit(A, meta(), b'{}')
    with pytest.raises(RetentionError):
        await repo.memory_link(A, rec['artifact_id'], 'summary')
    row = await repo.claim()
    await repo.complete(row, proof(row))
    with pytest.raises(RetentionError):
        await repo.memory_link(A, rec['artifact_id'], 'summary', proof_sha256='0' * 64)
    memory = await repo.memory_link(A, rec['artifact_id'], 'summary', proof_sha256=row['sha256'])
    assert memory['learned_model_state'] is False
    assert (await repo.memory_get(A, memory['memory_id']))['artifact']['archive_verified']
    with pytest.raises(RetentionError):
        await repo.memory_get(B, memory['memory_id'])


@pytest.mark.asyncio
async def test_revocation_prevents_inflight_worker_finalization(db):
    sessions, repo = db
    rec, _ = await repo.admit(A, meta(), b'{}')
    row = await repo.claim()
    await sql(sessions, 'UPDATE retention.membership SET active=false')
    assert not await repo.complete(row, proof(row))
    assert (await sql(sessions, 'SELECT available_at FROM retention.artifact'))[0]['available_at'] is None


@pytest.mark.asyncio
async def test_migration_rerun_and_transactional_reversal_preserve_legacy(db):
    sessions, repo = db
    await sql(sessions, 'CREATE TABLE public.retention_fixture_legacy(id integer PRIMARY KEY)')
    rec, _ = await repo.admit(A, meta(), b'{}')
    connection = await asyncpg.connect(guarded_dsn())
    try:
        for migration in MIGRATIONS:
            await connection.execute(migration.read_text(encoding='utf-8'))
        await connection.execute('BEGIN; DROP SCHEMA retention CASCADE; ROLLBACK;')
        assert await connection.fetchval('SELECT count(*) FROM retention.artifact') == 1
        assert await connection.fetchval("SELECT to_regclass('public.retention_fixture_legacy')")
        await connection.execute('DROP SCHEMA retention CASCADE')
        for migration in MIGRATIONS:
            await connection.execute(migration.read_text(encoding='utf-8'))
        assert await connection.fetchval('SELECT count(*) FROM retention.artifact') == 0
        assert await connection.fetchval("SELECT to_regclass('public.retention_fixture_legacy')")
    finally:
        await connection.execute('DROP TABLE public.retention_fixture_legacy')
        await connection.close()


@pytest.mark.asyncio
async def test_rejected_upload_has_durable_orphan_cleanup_without_availability(db):
    sessions, repo = db
    rec, _ = await repo.admit(A, meta(), b'{}')
    stale = await repo.claim()
    await repo.cancel(A, rec['job_id'])
    reference = proof(stale)
    assert not await repo.complete(stale, reference)
    assert await repo.register_orphan(stale, reference)
    assert await repo.register_orphan(stale, reference)
    assert (await sql(sessions, 'SELECT count(*) AS n FROM retention.orphan_archive'))[0]['n'] == 1
    assert (await sql(sessions, 'SELECT available_at,object_version FROM retention.artifact'))[0] == {
        'available_at': None, 'object_version': None}
    assert await repo.claim_orphan_purge() is None
    await sql(sessions, "UPDATE retention.artifact SET retention_until=now()-interval '1 second'")
    purge = await repo.claim_orphan_purge()
    assert purge['object_version'] == reference['version']
    deleted = {'bucket': purge['object_bucket'], 'key': purge['object_key'],
               'version': purge['object_version'], 'deleted': True}
    assert not await repo.complete_orphan_purge(purge, dict(deleted, deleted=False))
    assert not await repo.complete_orphan_purge(dict(purge, orphan_lease_token='stale'), deleted)
    assert await repo.complete_orphan_purge(purge, deleted)
    assert await repo.claim_orphan_purge() is None


@pytest.mark.asyncio
async def test_canonical_object_never_registered_as_orphan(db):
    sessions, repo = db
    await repo.admit(A, meta(), b'{}')
    row = await repo.claim()
    assert await repo.complete(row, proof(row))
    assert not await repo.register_orphan(row, proof(row))
    assert (await sql(sessions, 'SELECT count(*) AS n FROM retention.orphan_archive'))[0]['n'] == 0


@pytest.mark.asyncio
async def test_memory_search_filters_owner_grant_and_membership(db):
    sessions, repo = db
    rec, row = await verified(repo)
    linked = await repo.memory_link(A, rec['artifact_id'], 'Fixture fruiting evidence', proof_sha256=row['sha256'])
    assert (await repo.memory_list(A, 'fruiting'))[0]['memory_id'] == linked['memory_id']
    assert await repo.memory_list(A, 'missing') == []
    assert await repo.memory_list(B, 'fruiting') == []
    assert await repo.memory_list(OTHER, 'fruiting') == []
    await repo.delete(A, rec['artifact_id'])
    assert await repo.memory_list(A, 'fruiting') == []


@pytest.mark.asyncio
async def test_membership_share_lock_serializes_operator_revocation(db):
    sessions, repo = db
    async with sessions() as session, session.begin():
        await repo.authorize_in_session(session, A)
        async with sessions() as other, other.begin():
            await other.execute(text("SET LOCAL lock_timeout='50ms'"))
            with pytest.raises(Exception, match='lock timeout'):
                await other.execute(text('UPDATE retention.membership SET active=false WHERE subject=:s'),
                                    {'s': A.subject})
    await sql(sessions, 'UPDATE retention.membership SET active=false WHERE subject=:s', s=A.subject)
    with pytest.raises(RetentionError) as exc:
        await repo.require_membership(A)
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_repository_rejects_invalid_admission_without_partial_rows(db):
    sessions, repo = db
    for metadata, payload in ((meta(), b''), (meta(), b'x' * (CONFIG.max_payload_bytes + 1)),
                              ({**meta(), 'metadata_sha256': '0' * 64}, b'{}'),
                              ({**meta(), 'kind': 'public'}, b'{}')):
        with pytest.raises(RetentionError):
            await repo.admit(A, metadata, payload)
    assert (await sql(sessions, 'SELECT count(*) AS n FROM retention.artifact'))[0]['n'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('crash_before_commit', [True, False])
async def test_real_process_death_has_atomic_recovery(db, crash_before_commit):
    sessions, repo = db
    program = '''
import asyncio,json,os
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker,create_async_engine
from mindex_api.retention.contracts import Principal,RetentionConfig,admission_metadata
from mindex_api.retention.repository import RetentionRepository
class CrashSession(AsyncSession):
    async def execute(self, statement, *args, **kwargs):
        if os.environ.get('RETENTION_FIXTURE_CRASH_BEFORE') == '1' and 'INSERT INTO retention.outbox' in str(statement):
            print(json.dumps({'crashed':'before_outbox'}),flush=True)
            os._exit(24)
        return await super().execute(statement,*args,**kwargs)
async def main():
    dsn=os.environ['RETENTION_TEST_DSN'].replace('postgresql://','postgresql+asyncpg://',1)
    engine=create_async_engine(dsn)
    sessions=async_sessionmaker(engine,class_=CrashSession)
    config=RetentionConfig(enabled=True,bucket='fixture-private')
    repo=RetentionRepository(sessions,config)
    actor=Principal('https://issuer.test/auth/v1','user-a','tenant-a','project-a')
    rec,_=await repo.admit(actor,admission_metadata('dataset','process-crash','application/json',None,config),b'{}')
    print(json.dumps({'artifact_id':rec['artifact_id']}),flush=True)
    os._exit(23)
asyncio.run(main())
'''
    program = program.replace('tenant-a', A.tenant_id).replace('project-a', A.project_id)
    env = dict(os.environ, RETENTION_FIXTURE_CRASH_BEFORE='1' if crash_before_commit else '0')
    child = await asyncio.create_subprocess_exec(sys.executable, '-c', program, env=env,
        cwd=Path(__file__).parents[1], stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=20)
    assert child.returncode == (24 if crash_before_commit else 23), stderr.decode()
    report = json.loads(stdout.decode())
    if crash_before_commit:
        assert report['crashed'] == 'before_outbox'
        for table in ('artifact', 'job', 'outbox'):
            assert (await sql(sessions, f'SELECT count(*) AS n FROM retention.{table}'))[0]['n'] == 0
    else:
        row = await repo.claim()
        assert row['artifact_id'] == report['artifact_id']
        assert bytes(row['payload']) == b'{}'
        assert await repo.complete(row, proof(row))


@pytest.mark.asyncio
async def test_restricted_service_role_cannot_self_grant_or_reactivate(db):
    sessions, _ = db
    await sql(sessions, """DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='retention_fixture_service') THEN
            CREATE ROLE retention_fixture_service NOLOGIN;
        END IF; END $$""")
    await sql(sessions, 'GRANT USAGE ON SCHEMA retention TO retention_fixture_service')
    for table in ('artifact', 'job', 'outbox', 'memory_reference', 'orphan_archive'):
        await sql(sessions, f'GRANT SELECT,INSERT,UPDATE,DELETE ON retention.{table} TO retention_fixture_service')
    await sql(sessions, 'GRANT USAGE ON ALL SEQUENCES IN SCHEMA retention TO retention_fixture_service')
    await sql(sessions, 'GRANT SELECT ON retention.membership,retention.access_grant TO retention_fixture_service')
    await sql(sessions, 'GRANT UPDATE(updated_at) ON retention.membership TO retention_fixture_service')

    class RestrictedSession(Session):
        pass

    @event.listens_for(RestrictedSession, 'after_begin')
    def restricted_role(session, transaction, connection):
        connection.exec_driver_sql('SET LOCAL ROLE retention_fixture_service')

    restricted_sessions = async_sessionmaker(sessions.kw['bind'], class_=AsyncSession,
                                             sync_session_class=RestrictedSession)
    restricted = RetentionRepository(restricted_sessions, CONFIG)
    try:
        rec, _ = await restricted.admit(A, meta(), b'{}')
        assert (await restricted.get(A, rec['artifact_id']))['state'] == 'pending'
        for query in ('UPDATE retention.membership SET active=true',
                      'DELETE FROM retention.membership',
                      'UPDATE retention.membership SET subject=\'attacker\'',
                      'INSERT INTO retention.membership SELECT * FROM retention.membership',
                      'UPDATE retention.access_grant SET active=true'):
            with pytest.raises(Exception, match='permission denied'):
                await sql(restricted_sessions, query)
    finally:
        await sql(sessions, 'DROP OWNED BY retention_fixture_service')
        await sql(sessions, 'DROP ROLE retention_fixture_service')
