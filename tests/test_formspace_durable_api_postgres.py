"""Real signed JWT + PostgreSQL + loopback TS worker; synthetic S3 only.

Uses shared09's guarded RETENTION_TEST_DSN and a dedicated retention_fixture_ DB.
No deployed Supabase/AWS/MYCA/NAS claim. Local role and signing key are fixtures.
"""
import asyncio
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import text

from mindex_api.formspace.repository import FormSpaceRepository
from mindex_api.formspace.service import FormSpaceService
from mindex_api.retention.contracts import Principal
from mindex_api.retention.service import archive_one
from mindex_api.routers.formspace_durable import router as formspace_router
from test_retention_api import stack, db, USER_B, PROJECT_B, USER_A, ISSUER, TENANT, PROJECT
from test_retention_postgres import sql

BASE = '/api/mindex/formspace/v1'
FIXTURES = Path(__file__).parent / 'fixtures/formspace'


@pytest.mark.asyncio
async def test_signed_auth_real_database_actual_worker_and_verified_readback(stack, monkeypatch):
    website = os.getenv('FORMSPACE_WEBSITE_CHECKOUT')
    tsx = os.getenv('FORMSPACE_TSX_CLI')
    if not website or not tsx:
        pytest.skip('Explicit local website checkout and tsx CLI required for actual worker process')
    assert Path(website, 'scripts/formspace-worker.ts').is_file()
    assert Path(tsx).is_file()
    sessions = stack['sessions']
    async with sessions() as session:
        existing = (await session.execute(text("SELECT schema_name FROM information_schema.schemata WHERE schema_name='formspace'"))).all()
        assert not existing, 'Use a new dedicated fixture DB; existing FormSpace data is preserved'
        raw = await session.connection()
        driver = await raw.get_raw_connection()
        await driver.driver_connection.execute((Path(__file__).parents[1] / 'migrations/20261001_formspace_durable.sql').read_text(encoding='utf-8'))
    request = json.loads((FIXTURES/'chain-request.json').read_text(encoding='utf-8'))
    golden = (FIXTURES/'chain-result.json').read_bytes()
    code_hash = json.loads((FIXTURES/'chain-fixture-metadata.json').read_text())['engine_code_sha256']
    repository = FormSpaceRepository(stack['repo'], Principal)
    service = FormSpaceService(repository, stack['service'], code_hash)
    app = stack['app']
    app.state.formspace_service = service
    app.include_router(formspace_router, prefix='/api/mindex')
    token = 'synthetic-worker-token-for-disposable-fixture-only'
    monkeypatch.setenv('FORMSPACE_WORKER_TOKEN', token)
    http = stack['http']
    headers = {**stack['headers'](), 'Idempotency-Key':'actual-worker-path'}
    chart_definition = request['chart_revision']
    saved_chart = await http.post(BASE+'/charts', headers=headers, json=chart_definition)
    assert saved_chart.status_code == 200, saved_chart.text
    assert saved_chart.json()['definition'] == chart_definition
    listed_charts = await http.get(BASE+'/charts', headers=headers)
    assert listed_charts.status_code == 200 and listed_charts.json()['charts'] == [saved_chart.json()]
    exact_chart = await http.get(BASE+'/charts/'+chart_definition['chart_id'], headers=headers,
                                 params={'revision': chart_definition['revision']})
    assert exact_chart.status_code == 200 and exact_chart.json() == saved_chart.json()
    for denied in (stack['headers'](subject=USER_B), stack['headers'](project=PROJECT_B)):
        assert (await http.get(BASE+'/charts', headers=denied)).json()['charts'] == []
        assert (await http.get(BASE+'/charts/'+chart_definition['chart_id'], headers=denied,
                               params={'revision': chart_definition['revision']})).status_code == 404
    for bad in ({'X-User-Id':USER_A}, {**headers,'Authorization':'Bearer '+stack['token'](exp=int(time.time())-30)},
                {**headers,'Authorization':'Bearer '+stack['token'](aud='wrong')},
                {**headers,'Authorization':'Bearer '+stack['token'](role='service_role')}):
        assert (await http.get(BASE+'/jobs',headers=bad)).status_code == 401
    admitted = await http.post(BASE+'/jobs',headers=headers,json={'request':request})
    assert admitted.status_code == 202, admitted.text
    job_id = admitted.json()['job']['job_id']
    replay = await http.post(BASE+'/jobs',headers=headers,json={'request':request})
    assert replay.status_code == 200 and replay.json()['job']['job_id'] == job_id
    changed = json.loads(json.dumps(request)); changed['parameters']['dt']=.2
    assert (await http.post(BASE+'/jobs',headers=headers,json={'request':changed})).status_code == 409
    for denied in (stack['headers'](subject=USER_B),stack['headers'](project=PROJECT_B)):
        assert (await http.get(BASE+'/jobs',headers=denied)).json()['jobs'] == []
        for suffix in ('','/input','/result'):
            assert (await http.get(BASE+f'/jobs/{job_id}'+suffix,headers=denied)).status_code == 404
        assert (await http.post(BASE+f'/jobs/{job_id}/cancel',headers=denied)).status_code == 404
    assert (await http.get(BASE+f'/jobs/{job_id}/result',headers=headers)).status_code == 409
    inp=await http.get(BASE+f'/jobs/{job_id}/input',headers=headers)
    assert inp.content==(FIXTURES/'chain-request.json').read_bytes()
    assert inp.headers['x-input-durability']=='postgres_committed'
    assert inp.headers['x-input-sha256']==hashlib.sha256(inp.content).hexdigest()
    # Bind only an ephemeral loopback socket. Preserve every other process/port.
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(16);sock.setblocking(False)
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='off',access_log=False))
    serve=asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(100):
        if server.started:break
        await asyncio.sleep(.01)
    assert server.started
    env={**os.environ,'FORMSPACE_WORKER_API_URL':f'http://127.0.0.1:{port}{BASE}',
         'FORMSPACE_WORKER_TOKEN':token,'FORMSPACE_WORKER_ID':'brief04-fixture'}
    async def run_worker():
        proc=await asyncio.create_subprocess_exec('node',tsx,str(Path(website,'scripts/formspace-worker.ts')),
            cwd=website,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:out,err=await asyncio.wait_for(proc.communicate(),30)
        except TimeoutError:
            proc.kill();await proc.wait();raise
        assert proc.returncode==0,(out.decode(),err.decode())
        return json.loads(out.decode().strip())
    try:
        first=await run_worker()
        assert first['outcome']=='pending',first
        row=await repository.get(stack['principal'],job_id)
        assert row['state']=='archiving' and bytes(row['output_bytes'])==golden
        assert await archive_one(stack['repo'],stack['service'].object_store)
        # FakeS3 exercised through the real private adapter, not a verified label injection.
        await sql(sessions,"UPDATE formspace.outbox SET available_at=clock_timestamp() WHERE job_id=:id",id=job_id)
        second=await run_worker()
        assert second['outcome']=='completed',second
        done=(await http.get(BASE+f'/jobs/{job_id}',headers=headers)).json()['job']
        assert done['artifact']['state']=='verified'
        assert done['memory']['reference_state']=='verified'
        assert done['memory']['state']=='pending' and done['memory']['index_state']=='pending'
        assert done['replica']['state']=='unavailable'
        assert (await run_worker())['outcome']=='idle'
        output=await http.get(BASE+f'/jobs/{job_id}/result',headers=headers)
        assert output.status_code==200,output.text
        assert output.content==golden
        assert output.headers['x-content-sha256']==hashlib.sha256(golden).hexdigest()
        assert output.headers['x-artifact-version']
        duplicate=(await http.post(BASE+f'/jobs/{job_id}/memory',headers=headers)).json()['job']
        assert duplicate['memory']['memory_id']==done['memory']['memory_id']
        # Another process exits abruptly immediately after a committed admission.
        script='''import asyncio,json,os
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from mindex_api.retention.contracts import Principal,RetentionConfig
from mindex_api.retention.repository import RetentionRepository
from mindex_api.formspace.repository import FormSpaceRepository
async def main():
 e=create_async_engine(os.environ['RETENTION_TEST_DSN'].replace('postgresql://','postgresql+asyncpg://',1))
 s=async_sessionmaker(e,expire_on_commit=False)
 p=Principal(*json.loads(os.environ['FORMSPACE_FIXTURE_PRINCIPAL']))
 r=FormSpaceRepository(RetentionRepository(s,RetentionConfig(enabled=True)),Principal)
 with open(os.environ['FORMSPACE_FIXTURE_REQUEST'],encoding='utf-8') as f:q=json.load(f)
 await r.admit(p,'crash-after-commit',q)
 os._exit(24)
asyncio.run(main())'''
        child_env={**os.environ,'FORMSPACE_FIXTURE_PRINCIPAL':json.dumps([ISSUER,USER_A,TENANT,PROJECT]),'FORMSPACE_FIXTURE_REQUEST':str(FIXTURES/'chain-request.json')}
        proc=await asyncio.create_subprocess_exec(sys.executable,'-c',script,env=child_env,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(proc.communicate(),15)
        assert proc.returncode==24,(out.decode(),err.decode())
        crash_receipt,created=await FormSpaceRepository(stack['repo'],Principal).admit(stack['principal'],'crash-after-commit',request)
        assert not created
        crash_id=crash_receipt['job_id']
        assert (await http.post(BASE+f'/jobs/{crash_id}/cancel',headers=headers)).status_code==200
        assert (await http.get(BASE+f'/jobs/{crash_id}/input',headers=headers)).content==inp.content
        # Immediate revocation denies all bytes despite cached signing keys.
        await sql(sessions,'UPDATE retention.membership SET active=false WHERE subject=:s',s=USER_A)
        for suffix in ('','/input','/result'):
            assert (await http.get(BASE+f'/jobs/{job_id}'+suffix,headers=headers)).status_code==403
    finally:
        server.should_exit=True
        await asyncio.wait_for(serve,5)
        sock.close()
