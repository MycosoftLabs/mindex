"""Real parser/route/DTO modules, fake DB and governance; no live app or PostGIS."""
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from fastapi import HTTPException
from pydantic import BaseModel
import pytest

ROOT=Path(__file__).resolve().parents[1]
PREFIX='_batch9_api'


def source_path(relative):
    baseline=os.environ.get('BATCH9_BBOX_SOURCE_ROOT')
    candidate=Path(baseline)/relative if baseline else ROOT/relative
    return candidate if candidate.exists() else ROOT/relative


@pytest.fixture
def contract(monkeypatch):
    def module(name,**attrs):
        value=ModuleType(name);value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules,name,value);return value
    def load(name,relative):
        spec=importlib.util.spec_from_file_location(name,source_path(relative))
        value=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,name,value)
        spec.loader.exec_module(value);return value
    for suffix,path in [('', 'mindex_api'),('.routers','mindex_api/routers'),
                        ('.routers.worldview','mindex_api/routers/worldview'),('.schemas','mindex_api/schemas'),
                        ('.contracts','mindex_api/contracts'),('.contracts.v1','mindex_api/contracts/v1'),
                        ('.utils','mindex_api/utils')]:
        module(PREFIX+suffix,__path__=[str(ROOT/path)])
    class PaginationParams(BaseModel):
        limit:int
        offset:int
    class CallerIdentity(BaseModel):
        kind:str='fixture'
    def prohibited():raise AssertionError('No actual dependency resolution authorized')
    module(PREFIX+'.dependencies',PaginationParams=PaginationParams,pagination_params=prohibited,
           get_db_session=prohibited,require_api_key=prohibited)
    module(PREFIX+'.auth',CallerIdentity=CallerIdentity,require_worldview_key=prohibited)
    events=[];envelopes=[]
    module(PREFIX+'.utils.deep_agent_events',schedule_domain_event=lambda **kwargs:events.append(kwargs))
    async def wrap(**kwargs):
        envelopes.append(kwargs)
        return {'payload':kwargs['data'],'source_domains':kwargs['source_domains']}
    module(PREFIX+'.routers.worldview.response_envelope',wrap_governed_response=wrap)
    load(PREFIX+'.schemas.common','mindex_api/schemas/common.py')
    load(PREFIX+'.schemas.observations','mindex_api/schemas/observations.py')
    load(PREFIX+'.contracts.v1.observations','mindex_api/contracts/v1/observations.py')
    observations=load(PREFIX+'.routers.observations','mindex_api/routers/observations.py')
    overlays=load(PREFIX+'.routers.fungal_overlays','mindex_api/routers/fungal_overlays.py')
    worldview=load(PREFIX+'.routers.worldview.species','mindex_api/routers/worldview/species.py')
    yield SimpleNamespace(observations=observations,overlays=overlays,worldview=worldview,
                          events=events,envelopes=envelopes,PaginationParams=PaginationParams,CallerIdentity=CallerIdentity)
    # Shared helper may have been imported through __path__, not load().
    for name in list(sys.modules):
        if name.startswith(PREFIX+'.utils.bbox'):sys.modules.pop(name)


def finish(coro):
    try:yielded=coro.send(None)
    except StopIteration as result:return result.value
    finally:coro.close()
    raise AssertionError(f'Unexpected asynchronous suspension: {yielded!r}')


class FakeDB:
    def __init__(self,rows=(),failure=None):self.rows=list(rows);self.calls=[];self.failure=failure
    async def execute(self,statement,params):
        self.calls.append((str(statement),dict(params)))
        if self.failure:raise self.failure
        return self
    def mappings(self):return self
    def all(self):return self.rows
    def scalar_one(self):return 17


INVALID=['nan,-1,1,1','-1,nan,1,1','-1,-1,nan,1','-1,-1,1,nan',
         '-inf,-1,1,1','-1,-inf,1,1','-1,-1,inf,1','-1,-1,1,1e999',
         '-180.0001,-1,1,1','-1,-90.0001,1,1','-1,-1,180.0001,1','-1,-1,1,90.0001',
         '170,-10,-170,10','1,-1,-1,1','-1,1,1,-1','0,-1,0,1','-1,0,1,0',
         '1,2,3','1,2,3,4,5','bad,0,1,1','   ']


@pytest.mark.parametrize('parser',['observations','overlays'])
@pytest.mark.parametrize('bbox',INVALID)
def test_invalid_bbox_rejected_with_400(contract,parser,bbox):
    with pytest.raises(HTTPException) as raised:getattr(contract,parser)._parse_bbox(bbox)
    assert raised.value.status_code==400


@pytest.mark.parametrize('parser',['observations','overlays'])
@pytest.mark.parametrize('bbox,expected',[
    (None,None),('',None),('-180,-90,180,90',(-180.,-90.,180.,90.)),
    ('0,0,1,1',(0.,0.,1.,1.)),(' -1 , -1 , 0 , 0 ',(-1.,-1.,0.,0.)),
    ('-180,89,180,90',(-180.,89.,180.,90.)),('1e1,-2.5,11,0',(10.,-2.5,11.,0.))])
def test_valid_bbox_mapping_contract_preserved(contract,parser,bbox,expected):
    result=getattr(contract,parser)._parse_bbox(bbox)
    if expected is None:assert result is None
    else:assert result==dict(zip(('min_lon','min_lat','max_lon','max_lat'),expected))


def observation_call(contract,db,bbox,include_total=False):
    return contract.observations.list_observations(
        pagination=contract.PaginationParams(limit=5,offset=0),db=db,taxon_id=None,kingdom=None,
        start=None,end=None,bbox=bbox,include_total=include_total)


def overlay_call(contract,route,db,bbox):
    if route=='cells':return contract.overlays.get_fungal_overlay_cells(db=db,bbox=bbox,layer='mycelium',limit=5,resolution_deg=.25)
    if route=='samples':return contract.overlays.get_fungal_overlay_samples(db=db,bbox=bbox,limit=5)
    return contract.overlays.get_land_deployment_ranking(db=db,bbox=bbox,limit=5,mission='fixture')


@pytest.mark.parametrize('route',['observations','cells','samples','land'])
@pytest.mark.parametrize('bbox',['nan,-1,1,1','-1,-1,181,1','170,-10,-170,10'])
def test_invalid_route_bbox_never_reaches_sql_or_event(contract,route,bbox):
    db=FakeDB()
    coro=observation_call(contract,db,bbox) if route=='observations' else overlay_call(contract,route,db,bbox)
    with pytest.raises(HTTPException) as raised:finish(coro)
    assert raised.value.status_code==400 and db.calls==[] and contract.events==[]


@pytest.mark.parametrize('route',['cells','samples','land'])
def test_overlay_valid_bounds_bind_unchanged_and_keep_response_shape(contract,route):
    db=FakeDB()
    result=finish(overlay_call(contract,route,db,'0,0,1,1'))
    assert len(db.calls)==1
    assert {k:db.calls[0][1][k] for k in ('min_lon','min_lat','max_lon','max_lat')}=={
        'min_lon':0.,'min_lat':0.,'max_lon':1.,'max_lat':1.}
    if route!='land':
        assert result.data==[] and result.meta['bbox']=={'min_lon':0.,'min_lat':0.,'max_lon':1.,'max_lat':1.}
        assert result.meta['count']==0 and result.meta['source']=='mindex.obs.observation'
    else:assert result['results']==[] and result['count']==0 and result['mission']=='fixture'


def raw_observation():
    return dict(id='00000000-0000-0000-0000-000000000001',source='fixture',source_id='one',
                observed_at='2026-09-30T00:00:00Z',metadata={'verified_fixture':True},
                latitude=0.,longitude=1.)


@pytest.mark.parametrize('bbox',[None,'','0,0,1,1','-180,-90,180,90'])
def test_worldview_calls_actual_observations_without_query_objects_or_implicit_count(contract,bbox):
    db=FakeDB([raw_observation()]);request=SimpleNamespace(state=SimpleNamespace());caller=contract.CallerIdentity()
    result=finish(contract.worldview.worldview_list_observations(
        request=request,taxon_id=None,start=None,end=None,bbox=bbox,limit=5,offset=0,caller=caller,db=db))
    assert len(db.calls)==1 and 'SELECT count(*)' not in db.calls[0][0]
    assert result['payload']['pagination']=={'limit':5,'offset':0,'total':None}
    row=result['payload']['data'][0]
    assert row['source_id']=='one' and row['location']['coordinates']==[1.,0.]
    assert row['metadata']=={'verified_fixture':True}
    assert result['source_domains']==['observations','species']
    assert request.state.caller_identity is caller and len(contract.envelopes)==1 and len(contract.events)==1


@pytest.mark.parametrize('bbox',['nan,-1,1,1','-1,-91,1,1'])
def test_worldview_propagates_bad_bounds_without_db_event_or_envelope(contract,bbox):
    db=FakeDB()
    with pytest.raises(HTTPException) as raised:
        finish(contract.worldview.worldview_list_observations(
            request=SimpleNamespace(state=SimpleNamespace()),taxon_id=None,start=None,end=None,bbox=bbox,
            limit=5,offset=0,caller=contract.CallerIdentity(),db=db))
    assert raised.value.status_code==400 and db.calls==[] and contract.events==[] and contract.envelopes==[]


def test_explicit_internal_total_remains_supported(contract):
    db=FakeDB()
    result=finish(observation_call(contract,db,'0,0,1,1',include_total=True))
    assert len(db.calls)==2 and 'SELECT count(*)' in db.calls[1][0]
    assert db.calls[0][1]==db.calls[1][1] and result.pagination.total==17


def test_worldview_database_failure_is_not_wrapped_as_success(contract):
    db=FakeDB(failure=RuntimeError('fixture SQL failure'))
    with pytest.raises(RuntimeError,match='fixture SQL failure'):
        finish(contract.worldview.worldview_list_observations(
            request=SimpleNamespace(state=SimpleNamespace()),taxon_id=None,start=None,end=None,bbox=None,
            limit=5,offset=0,caller=contract.CallerIdentity(),db=db))
    assert len(db.calls)==1 and contract.events==[] and contract.envelopes==[]
