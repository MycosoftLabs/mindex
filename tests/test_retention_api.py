"""Signed JWT -> actual FastAPI -> disposable PostgreSQL -> fake S3 -> MYCA.

No live identity provider or object-store contact. Run after database suite with
the same explicit disposable fixture guard; tests recreate retention schema.
"""
import hashlib
import json
import time
from dataclasses import replace

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from sqlalchemy import text

from mindex_api.retention.client import MycaMemoryAdapter, RetentionClient
from mindex_api.retention.contracts import Principal, RetentionError
from mindex_api.retention.identity import IdentityVerifier
from mindex_api.retention.object_store import PrivateObjectStore
from mindex_api.retention.repository import RetentionRepository
from mindex_api.retention.service import RetentionService, archive_one
from mindex_api.routers.retention import router
from test_retention_postgres import db, sql
from test_retention_object_store import FakeS3

TENANT = '11111111-1111-4111-8111-111111111111'
PROJECT = '22222222-2222-4222-8222-222222222222'
USER_A = '33333333-3333-4333-8333-333333333333'
USER_B = '44444444-4444-4444-8444-444444444444'
PROJECT_B = '55555555-5555-4555-8555-555555555555'
TENANT_B = '66666666-6666-4666-8666-666666666666'
ISSUER = 'https://fixture.supabase.test/auth/v1'
BASE = '/api/mindex/retention/v1'


@pytest_asyncio.fixture
async def stack(db):
    sessions, old_repo = db
    cfg = replace(old_repo.config, bucket='private-fixture-bucket', expected_owner='123456789012',
                  region='us-west-2', kms_key='arn:aws:kms:us-west-2:123456789012:key/test-key')
    repo = RetentionRepository(sessions, cfg)
    principals = [Principal(ISSUER, USER_A, TENANT, PROJECT), Principal(ISSUER, USER_B, TENANT, PROJECT),
                  Principal(ISSUER, USER_A, TENANT, PROJECT_B), Principal(ISSUER, USER_A, TENANT_B, PROJECT_B)]
    for p in principals:
        await sql(sessions, 'INSERT INTO retention.membership (issuer,subject,tenant_id,project_id,active) VALUES (:i,:s,:t,:p,true)',
                  i=p.issuer, s=p.subject, t=p.tenant_id, p=p.project_id)
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='fixture-key', alg='ES256', use='sig')
    async def keys(): return {'keys': [jwk]}
    verifier = IdentityVerifier(issuer=ISSUER, audience='authenticated', enabled=True, key_provider=keys)
    store_client = FakeS3()
    service = RetentionService(repo, verifier, PrivateObjectStore(store_client, cfg), cfg)
    app = FastAPI(); app.include_router(router, prefix='/api/mindex'); app.state.retention_service = service
    def token(subject=USER_A, **changes):
        now = int(time.time())
        return jwt.encode({'iss': ISSUER, 'sub': subject, 'aud': 'authenticated', 'role': 'authenticated',
            'is_anonymous': False, 'iat': now, 'exp': now+300, **changes}, key, algorithm='ES256', headers={'kid': 'fixture-key'})
    def headers(subject=USER_A, tenant=TENANT, project=PROJECT):
        return {'Authorization': 'Bearer '+token(subject), 'X-Tenant-Id': tenant, 'X-Project-Id': project}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as http:
        yield {'http': http, 'service': service, 'repo': repo, 'sessions': sessions, 'store': store_client,
               'headers': headers, 'token': token, 'app': app, 'principal': principals[0], 'config': cfg}


async def admit(stack, key='api-fixture', payload=b'{"synthetic":true}'):
    response = await stack['http'].post(BASE+'/artifacts', content=payload, headers={**stack['headers'](),
        'Idempotency-Key': key, 'Content-Type': 'application/json', 'X-Artifact-Kind': 'dataset',
        'X-Source-Event-At': '2026-09-01T01:00:00Z'})
    assert response.status_code == 202, response.text
    return response.json()


@pytest.mark.asyncio
async def test_signed_private_dataset_to_verified_memory_full_path(stack):
    async with RetentionClient('http://localhost', access_token=stack['token'](), tenant_id=TENANT,
            project_id=PROJECT, transport=httpx.ASGITransport(app=stack['app'])) as client:
        principal = await client.principal()
        assert principal['subject'] == USER_A
        row = await client.admit(b'{"synthetic":true}', idempotency_key='full-path', kind='dataset', media_type='application/json')
        assert row['state'] == 'pending' and row['archive_verified'] is False
        with pytest.raises(RetentionError) as pending:
            await client.content(row['artifact_id'])
        assert pending.value.status == 409
        assert await archive_one(stack['repo'], stack['service'].object_store)
        receipt, payload = await client.content(row['artifact_id'])
        assert payload == b'{"synthetic":true}' and receipt['archive_verified']
        assert receipt['received_at'] <= receipt['available_at']
        adapter = MycaMemoryAdapter(client)
        memory = await adapter.remember(row['artifact_id'], 'Synthetic dataset exact reference')
        recalled, exact = await adapter.recall(memory['memory_id'])
        assert recalled == memory and exact == payload and recalled['learned'] is False
        # The generic reference is canonical in MINDEX, not a model-weight write.
        assert memory['artifact_sha256'] == hashlib.sha256(payload).hexdigest()
        duplicate = await adapter.remember(row['artifact_id'], 'Synthetic dataset exact reference')
        assert duplicate['memory_id'] == memory['memory_id']


@pytest.mark.asyncio
async def test_bearer_required_unsigned_owner_service_role_expired_denied(stack):
    bad_headers = [{}, {'X-User-Id': USER_A, 'X-Internal-Token': 'fixture-service'},
        {**stack['headers'](), 'Authorization': 'Bearer '+stack['token'](role='service_role')},
        {**stack['headers'](), 'Authorization': 'Bearer '+stack['token'](exp=int(time.time())-60)},
        {**stack['headers'](), 'Authorization': 'Bearer '+stack['token'](aud='wrong')}]
    for headers in bad_headers:
        response = await stack['http'].get(BASE+'/principal', headers=headers)
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'private, no-store'


@pytest.mark.asyncio
async def test_cross_user_project_tenant_all_access_paths_denied(stack):
    row = await admit(stack)
    await archive_one(stack['repo'], stack['service'].object_store)
    memory = await stack['http'].post(BASE+'/memories', headers=stack['headers'](), json={
        'artifact_id': row['artifact_id'], 'summary': 'private synthetic'})
    assert memory.status_code == 200, memory.text
    mid = memory.json()['memory_id']
    for headers in (stack['headers'](subject=USER_B), stack['headers'](project=PROJECT_B),
                    stack['headers'](tenant=TENANT_B, project=PROJECT_B)):
        for path in (f'/artifacts/{row["artifact_id"]}', f'/artifacts/{row["artifact_id"]}/content',
                     f'/artifacts/{row["artifact_id"]}/events', f'/jobs/{row["job_id"]}', f'/memories/{mid}'):
            response = await stack['http'].get(BASE+path, headers=headers)
            assert response.status_code == 404, (path,response.text)
        for path in ('/artifacts?query='+row['artifact_id'], '/memories?query=private'):
            response = await stack['http'].get(BASE+path, headers=headers)
            assert response.json()['items'] == []
        response = await stack['http'].post(BASE+f'/jobs/{row["job_id"]}/cancel', headers=headers)
        assert response.status_code == 404
        response = await stack['http'].delete(BASE+f'/artifacts/{row["artifact_id"]}', headers=headers)
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_revocation_after_cached_jwks_and_download_denies(stack):
    row = await admit(stack); await archive_one(stack['repo'], stack['service'].object_store)
    path=BASE+f'/artifacts/{row["artifact_id"]}/content'
    assert (await stack['http'].get(path, headers=stack['headers']())).status_code == 200
    await sql(stack['sessions'], 'UPDATE retention.membership SET active=false WHERE subject=:s', s=USER_A)
    for target in (path, BASE+'/principal', BASE+'/artifacts'):
        response = await stack['http'].get(target, headers=stack['headers']())
        assert response.status_code == 403 and 'synthetic' not in response.text


@pytest.mark.asyncio
async def test_delete_revokes_memory_and_never_claims_physical_erasure(stack):
    row=await admit(stack); await archive_one(stack['repo'], stack['service'].object_store)
    memory=await stack['service'].remember(stack['principal'],row['artifact_id'],'private synthetic')
    response=await stack['http'].delete(BASE+f'/artifacts/{row["artifact_id"]}',headers=stack['headers']())
    assert response.status_code==200 and response.json()['physical_deletion_pending'] is True
    assert len(stack['store'].objects)==1
    for path in (f'/artifacts/{row["artifact_id"]}/content',f'/memories/{memory["memory_id"]}'):
        assert (await stack['http'].get(BASE+path,headers=stack['headers']())).status_code==404


@pytest.mark.asyncio
async def test_malformed_oversized_and_changed_replay_preserve_source(stack):
    row=await admit(stack)
    response=await stack['http'].post(BASE+'/artifacts',headers={**stack['headers'](),'Idempotency-Key':'api-fixture','Content-Type':'application/json','X-Artifact-Kind':'dataset','X-Source-Event-At':'2026-09-01T01:00:00Z'},content=b'changed')
    assert response.status_code==409
    stack['service'].config=replace(stack['config'],max_payload_bytes=2)
    response=await stack['http'].post(BASE+'/artifacts',headers={**stack['headers'](),'Idempotency-Key':'oversize','Content-Type':'application/json'},content=b'123')
    assert response.status_code==413
    response=await stack['http'].post(BASE+'/memories',headers=stack['headers'](),content=b'{broken')
    assert response.status_code==422
    original=await stack['repo'].get(stack['principal'],row['artifact_id'])
    assert bytes(original['payload'])==b'{"synthetic":true}'


@pytest.mark.asyncio
async def test_corrupt_archive_quarantines_and_never_makes_available(stack):
    row=await admit(stack)
    stack['store'].payload_override=b'corrupted private bytes'
    await archive_one(stack['repo'],stack['service'].object_store)
    response=await stack['http'].get(BASE+f'/artifacts/{row["artifact_id"]}',headers=stack['headers']())
    assert response.json()['state']=='quarantined' and not response.json()['archive_verified']
    assert response.json()['available_at'] is None
    response=await stack['http'].get(BASE+f'/artifacts/{row["artifact_id"]}/content',headers=stack['headers']())
    assert response.status_code==409
    assert 'corrupted' not in response.text


@pytest.mark.asyncio
async def test_membership_revoked_during_archive_read_does_not_return_bytes(stack):
    row=await admit(stack); await archive_one(stack['repo'],stack['service'].object_store)
    real_store=stack['service'].object_store
    # A task-safe independent connection models revocation during external I/O.
    import asyncio
    loop=asyncio.get_running_loop()
    class RevokingStore:
        def read(self, record):
            payload=real_store.read(record)
            asyncio.run_coroutine_threadsafe(sql(stack['sessions'],
                'UPDATE retention.membership SET active=false WHERE subject=:s',s=USER_A),loop).result(5)
            return payload
    stack['service'].object_store=RevokingStore()
    response=await stack['http'].get(BASE+f'/artifacts/{row["artifact_id"]}/content',headers=stack['headers']())
    assert response.status_code==403 and 'synthetic' not in response.text


@pytest.mark.asyncio
async def test_missing_schema_returns_sanitized_unavailable(stack):
    await sql(stack['sessions'],'DROP TABLE retention.membership CASCADE')
    response=await stack['http'].get(BASE+'/principal',headers=stack['headers']())
    assert response.status_code==503
    assert response.json()=={'contract_version':'retention.v1','error':'retention_unavailable'}
