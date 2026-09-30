"""Actual ETL control flow, fake upstream/SQL mapping, real temporary SQLite commits.

No application initializer, production settings, psycopg connection, or network.
SQLite is a transaction-order fixture, not PostgreSQL/PostGIS SQL validation.
"""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '_batch8_etl'


def source_path(path):
    # Optional immutable effective baseline for reproducing the same final cases.
    baseline = os.environ.get('BATCH8_INAT_SOURCE_ROOT')
    candidate = Path(baseline)/path if baseline else ROOT/path
    return candidate if candidate.exists() else ROOT/path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, source_path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(tmp_path, monkeypatch):
    def module(name, **attrs):
        value = ModuleType(name)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, value)
        return value
    for name in (PREFIX, PREFIX+'.jobs', PREFIX+'.sources'):
        module(name, __path__=[])
    settings = SimpleNamespace(inat_base_url='https://fixture.invalid/v1', inat_api_token='',
                               inat_domain_mode='fungi', inat_rate_limit=0, http_timeout=1,
                               database_url='postgresql://fixture.invalid/database-one')
    module(PREFIX+'.config', settings=settings)
    def prohibited(*args, **kwargs):
        raise AssertionError('Production connection prohibited')
    module('psycopg', connect=prohibited, Connection=object)
    module('psycopg.rows', dict_row=object())
    db = load(PREFIX+'.db', 'mindex_etl/db.py')
    checkpoint = load(PREFIX+'.checkpoint', 'mindex_etl/checkpoint.py')
    monkeypatch.setattr(checkpoint, 'CHECKPOINT_DIR', tmp_path/'checkpoints')
    database = tmp_path/'fixture.sqlite'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE facts(kind TEXT, id TEXT, PRIMARY KEY(kind,id))')
    state = SimpleNamespace(calls=[], commits=0, fail_commit=None, ambiguous=False,
                            fail_id=None, checkpoint=checkpoint, database=database)
    def put(conn, kind, identity):
        if str(identity) == state.fail_id:
            raise RuntimeError('fixture row failure')
        conn.real.execute('INSERT OR REPLACE INTO facts VALUES(?,?)', (kind,str(identity)))
    class Connection:
        def __init__(self): self.real=sqlite3.connect(database); self.sql=''; self.args=()
        def commit(self):
            state.commits += 1
            if state.commits == state.fail_commit:
                if state.ambiguous: self.real.commit()
                raise RuntimeError('fixture commit failure')
            self.real.commit()
        def rollback(self): self.real.rollback()
        def close(self): self.real.close()
        def cursor(self): return self
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def execute(self,sql,args=()):
            self.sql=' '.join(sql.split()); self.args=args
            assert sql.count('%s') == len(args)
            if self.sql.startswith('INSERT INTO obs.observation'): put(self,'obs',args[2])
            elif self.sql.startswith('UPDATE obs.observation'): put(self,'obs',args[-1])
            else: assert self.sql.startswith('SELECT 1 FROM obs.observation')
        def fetchone(self):
            return self.real.execute('SELECT id FROM facts WHERE kind=? AND id=?',('obs',str(self.args[-1]))).fetchone()
    monkeypatch.setattr(db,'get_connection',Connection)
    def upsert(conn,**payload):
        name=payload['canonical_name']
        if not name: raise ValueError('Taxon name cannot be empty')
        put(conn,'taxon',name)
        return name
    module(PREFIX+'.taxon_canonicalizer', upsert_taxon=upsert,
           link_external_id=lambda conn,**kw:put(conn,'external',kw['external_id']))
    module(PREFIX+'.jobs.species_map_sync',upsert_species_map_rows=lambda conn,obs,**kw:put(conn,'map',obs['source_id']))
    source=load(PREFIX+'.sources.inat','mindex_etl/sources/inat.py')
    sys.modules[PREFIX+'.sources'].inat=source
    taxa=load(PREFIX+'.jobs.sync_inat_taxa','mindex_etl/jobs/sync_inat_taxa.py')
    obs=load(PREFIX+'.jobs.sync_inat_observations','mindex_etl/jobs/sync_inat_observations.py')
    state.records=[]
    class Client:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def close(self): pass
        def get(self,url,*,params,**kwargs):
            state.calls.append(dict(params))
            page,size=params['page'],params['per_page']
            body={'results':state.records[(page-1)*size:page*size],'total_results':len(state.records)}
            return httpx.Response(200,json=body,request=httpx.Request('GET',url))
    monkeypatch.setattr(source.httpx,'Client',Client)
    monkeypatch.setattr(source.time,'sleep',lambda _:None)
    monkeypatch.setattr(source,'save_to_local',lambda *a,**k:'fixture-no-write')
    state.source, state.taxa, state.obs, state.settings = source,taxa,obs,settings
    state.manager=lambda:checkpoint.CheckpointManager('fixture')
    def visible(kind):
        with sqlite3.connect(database) as connection:
            return [r[0] for r in connection.execute('SELECT id FROM facts WHERE kind=? ORDER BY id',(kind,))]
    state.visible=visible
    yield state
    for name in list(sys.modules):
        if name==PREFIX or name.startswith(PREFIX+'.'): sys.modules.pop(name)


def records(kind,count,missing=None):
    if kind=='taxa': return [{'id':i,'name':f'name-{i}','rank':'species'} for i in range(1,count+1)]
    return [{'id':i,'geojson':{'coordinates':[0,0]},
             'taxon':{'id':i,'name':None if i==missing else f'name-{i}','iconic_taxon_name':'Fungi'}}
            for i in range(1,count+1)]


def run(env,kind,manager=None,**kwargs):
    fn=env.taxa.sync_inat_taxa if kind=='taxa' else env.obs.sync_inat_observations
    if kind=='obs': kwargs['backfill_records']=0
    return fn(checkpoint_manager=manager,**kwargs)


@pytest.mark.parametrize('kind',['taxa','obs'])
def test_real_page_checkpoint_and_resume(env,kind):
    env.records=records(kind,5)
    manager=env.manager()
    assert run(env,kind,manager,per_page=2,max_pages=1)==2
    assert manager.load()['page']==1
    env.calls.clear()
    assert run(env,kind,manager,per_page=2,max_pages=3)==3
    assert env.calls[0]['page']==2
    assert manager.load()['page']==3
    assert env.visible('external' if kind=='taxa' else 'obs')==['1','2','3','4','5']


@pytest.mark.parametrize('kind',['taxa','obs'])
def test_explicit_start_page_and_absolute_limit(env,kind):
    env.records=records(kind,8)
    assert run(env,kind,env.manager(),per_page=2,start_page=3,max_pages=3)==2
    assert [c['page'] for c in env.calls]==[3]


@pytest.mark.parametrize('kind',['taxa','obs'])
def test_commit_failure_never_publishes_uncommitted_page(env,kind):
    # Old taxa publishes at 10 rows; old observations publishes at 1000 rows.
    env.records=records(kind,1000 if kind=='obs' else 10)
    env.fail_commit=1
    manager=env.manager()
    with pytest.raises(RuntimeError,match='commit failure'): run(env,kind,manager,per_page=1)
    assert not manager.exists()
    assert env.visible('external' if kind=='taxa' else 'obs')==[]


@pytest.mark.parametrize('kind',['taxa','obs'])
def test_page_two_row_failure_keeps_page_one_and_replays_page_two(env,kind):
    env.records=records(kind,4); env.fail_id='name-4'
    manager=env.manager()
    with pytest.raises(RuntimeError,match='row failure'): run(env,kind,manager,per_page=2)
    assert manager.load()['page']==1
    assert env.visible('external' if kind=='taxa' else 'obs')==['1','2']
    env.fail_id=None; env.calls.clear()
    assert run(env,kind,manager,per_page=2)==2
    assert env.calls[0]['page']==2


@pytest.mark.parametrize('kind',['taxa','obs'])
def test_commit_succeeds_checkpoint_fails_then_same_page_replays(env,kind,monkeypatch):
    env.records=records(kind,4); manager=env.manager()
    run(env,kind,manager,per_page=2,max_pages=1)
    before=manager.checkpoint_file.read_bytes()
    def fail(*a,**kw): raise OSError('fixture replace failure')
    with monkeypatch.context() as patch:
        patch.setattr(env.checkpoint.os,'replace',fail)
        with pytest.raises(OSError,match='replace failure'):run(env,kind,manager,per_page=2,max_pages=2)
    assert manager.checkpoint_file.read_bytes()==before
    assert env.visible('external' if kind=='taxa' else 'obs')==['1','2','3','4']
    assert list(manager.checkpoint_file.parent.iterdir())==[manager.checkpoint_file]
    env.calls.clear()
    assert run(env,kind,manager,per_page=2,max_pages=2)==2
    assert env.calls[0]['page']==2


@pytest.mark.parametrize('kind',['taxa','obs'])
def test_ambiguous_commit_is_replayed_not_skipped(env,kind):
    env.records=records(kind,2); env.fail_commit=1; env.ambiguous=True
    manager=env.manager()
    with pytest.raises(RuntimeError): run(env,kind,manager,per_page=2)
    assert not manager.exists()
    assert env.visible('external' if kind=='taxa' else 'obs')==['1','2']
    env.fail_commit=None; env.calls.clear()
    assert run(env,kind,manager,per_page=2)==2
    assert env.calls[0]['page']==1


def test_skipped_observations_still_complete_exact_fetched_page(env):
    env.records=records('obs',3,missing=2); manager=env.manager()
    assert run(env,'obs',manager,per_page=2)==2
    assert manager.load()['page']==2
    assert env.visible('obs')==['1','3']


@pytest.mark.parametrize('kind',['taxa','obs'])
def test_empty_result_never_invents_completed_page(env,kind):
    manager=env.manager()
    assert run(env,kind,manager,per_page=2)==0
    assert not manager.exists()


@pytest.mark.parametrize('change',[{'per_page':3},{'domain_mode':'all'},{'quality_grade':'casual'},
                                  {'updated_since':'2026-01-01T00:00:00Z'}])
def test_observation_query_mismatch_fails_before_fetch(env,change):
    env.records=records('obs',4); manager=env.manager()
    run(env,'obs',manager,per_page=2,max_pages=1)
    env.calls.clear(); args={'per_page':2}; args.update(change)
    with pytest.raises(ValueError,match='query'): run(env,'obs',manager,**args)
    assert env.calls==[]


@pytest.mark.parametrize('value',['{bad json',json.dumps({'page':10}),json.dumps([])])
def test_untrusted_checkpoint_fails_without_fetch_or_rows(env,value):
    manager=env.manager(); manager.checkpoint_file.write_text(value)
    env.records=records('taxa',4)
    with pytest.raises(ValueError):run(env,'taxa',manager,per_page=2)
    assert env.calls==[]; assert env.visible('external')==[]


@pytest.mark.parametrize('field,value',[('page',True),('page',0),('page',1.5),('committed',False),
                                       ('schema_version',999),('job_name','different')])
def test_tampered_checkpoint_shape_rejected(env,field,value):
    env.records=records('taxa',2); manager=env.manager()
    run(env,'taxa',manager,per_page=2)
    payload=json.loads(manager.checkpoint_file.read_text()); payload[field]=value
    manager.checkpoint_file.write_text(json.dumps(payload)); env.calls.clear()
    with pytest.raises(ValueError):run(env,'taxa',manager,per_page=2)
    assert env.calls==[]


def test_conflicting_explicit_start_rejected(env):
    env.records=records('taxa',2); manager=env.manager()
    run(env,'taxa',manager,per_page=2)
    env.calls.clear()
    with pytest.raises(ValueError,match='start_page'):run(env,'taxa',manager,per_page=2,start_page=5)
    assert env.calls==[]


def test_effective_taxa_page_size_is_fingerprinted(env):
    env.records=records('taxa',201); manager=env.manager()
    assert run(env,'taxa',manager,per_page=300,max_pages=1)==200
    assert run(env,'taxa',manager,per_page=200,max_pages=2)==1
    assert manager.load()['page']==2


def test_callback_only_after_consumer_exhausts_fetched_page(env):
    env.records=records('taxa',3); seen=[]
    it=env.source.iter_inat_taxa(per_page=2,save_locally=False,on_page=seen.append)
    next(it); assert seen==[]
    next(it); assert seen==[]
    next(it); assert seen==[1]
    it.close(); assert seen==[1]


@pytest.mark.parametrize('kind',['taxa','obs'])
def test_start_past_absolute_limit_performs_no_fetch(env,kind):
    env.records=records(kind,5)
    assert run(env,kind,per_page=2,start_page=3,max_pages=2)==0
    assert env.calls==[]


def test_resume_helper_passes_filters_and_manager_even_on_first_run(env):
    captured={}
    def sync(**kwargs): captured.update(kwargs);return 7
    assert env.checkpoint.resume_from_checkpoint('fixture',sync,per_page=17,max_pages=3)==7
    assert captured['per_page']==17 and captured['max_pages']==3
    assert isinstance(captured['checkpoint_manager'],env.checkpoint.CheckpointManager)
    assert 'start_page' not in captured  # selected job validates its query before deciding cursor


def test_full_sync_wrapper_enables_first_run_without_running_composition(env,monkeypatch):
    import ast
    source=source_path('scripts/full_fungi_sync_v2.py').read_text(encoding='utf-8')
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='sync_with_checkpoint')
    namespace={'CheckpointManager':env.checkpoint.CheckpointManager,
               'resume_from_checkpoint':env.checkpoint.resume_from_checkpoint,'log':lambda _:None}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<selected-wrapper>','exec'),namespace)
    captured={}
    def sync(**kwargs):captured.update(kwargs);return 3
    assert namespace['sync_with_checkpoint']('fixture',sync,per_page=17)==3
    assert isinstance(captured.get('checkpoint_manager'),env.checkpoint.CheckpointManager)


@pytest.mark.parametrize('kind',['taxa','obs'])
def test_database_target_change_cannot_reuse_cursor(env,kind):
    env.records=records(kind,4); manager=env.manager()
    run(env,kind,manager,per_page=2,max_pages=1)
    env.settings.database_url='postgresql://fixture.invalid/database-two'
    env.calls.clear()
    with pytest.raises(ValueError,match='query'):run(env,kind,manager,per_page=2)
    assert env.calls==[]
    assert 'database-one' not in manager.checkpoint_file.read_text()


def test_backfill_failure_keeps_committed_page_cursor(env,monkeypatch):
    env.records=records('obs',2); manager=env.manager()
    def backfill(conn,**kwargs):
        conn.real.execute('INSERT INTO facts VALUES(?,?)',('obs','backfill-uncommitted'))
        raise RuntimeError('backfill failure')
    monkeypatch.setattr(env.obs,'backfill_missing_inat_observation_metadata',backfill)
    with pytest.raises(RuntimeError,match='backfill failure'):
        env.obs.sync_inat_observations(checkpoint_manager=manager,per_page=2,backfill_records=1)
    assert manager.load()['page']==1
    assert env.visible('obs')==['1','2']


def test_missing_taxon_name_rolls_back_that_page_without_cursor(env):
    env.records=records('taxa',2); env.records[1]['name']=None
    manager=env.manager()
    with pytest.raises(ValueError,match='Taxon name'):run(env,'taxa',manager,per_page=2)
    assert env.visible('external')==[] and not manager.exists()


def test_checkpoint_serialization_failure_preserves_previous_bytes(env):
    env.records=records('taxa',2); manager=env.manager()
    run(env,'taxa',manager,per_page=2)
    before=manager.checkpoint_file.read_bytes()
    with pytest.raises(TypeError):manager.save_committed(2,query={'x':1},bad=object())
    assert manager.checkpoint_file.read_bytes()==before


def test_checkpoint_read_permission_failure_propagates_before_fetch(env,monkeypatch):
    import builtins
    env.records=records('taxa',2); manager=env.manager()
    run(env,'taxa',manager,per_page=2); env.calls.clear()
    real_open=builtins.open
    def guarded(file,*args,**kwargs):
        if Path(file)==manager.checkpoint_file:raise PermissionError('fixture unreadable')
        return real_open(file,*args,**kwargs)
    monkeypatch.setattr(builtins,'open',guarded)
    with pytest.raises(PermissionError):run(env,'taxa',manager,per_page=2)
    assert env.calls==[]


@pytest.mark.parametrize('kwargs',[{'per_page':0},{'per_page':True},{'start_page':0},
                                   {'start_page':1.5},{'max_pages':0},{'max_pages':True}])
def test_invalid_page_arguments_rejected_before_fetch(env,kwargs):
    env.records=records('taxa',2)
    with pytest.raises(ValueError):run(env,'taxa',env.manager(),**kwargs)
    assert env.calls==[]
