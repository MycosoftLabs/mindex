"""Native PostGIS tests for actual route SQL; synthetic database only, no app startup.

Run with MAP_FIXTURE_DSN targeting a dedicated database literally named map_fixture.
All writes are confined to that explicitly supplied fixture database.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import sys
import time
from types import ModuleType
import unittest
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "_viewport_fixture"


def load_route():
    def module(name, **attrs):
        obj = ModuleType(name); obj.__dict__.update(attrs); sys.modules[name] = obj
        return obj
    for suffix, directory in [("", "mindex_api"), (".routers", "mindex_api/routers"),
                              (".contracts", "mindex_api/contracts"), (".contracts.v1", "mindex_api/contracts/v1"),
                              (".schemas", "mindex_api/schemas"), (".utils", "mindex_api/utils")]:
        module(PREFIX + suffix, __path__=[str(ROOT / directory)])
    class PaginationParams(BaseModel):
        limit: int
        offset: int
    def prohibited():
        raise AssertionError("Live auth/database dependency resolution is prohibited")
    module(PREFIX + ".dependencies", PaginationParams=PaginationParams, pagination_params=prohibited,
           get_db_session=prohibited, require_api_key=prohibited)
    module(PREFIX + ".utils.deep_agent_events", schedule_domain_event=lambda **_: None)
    path = ROOT / "mindex_api/routers/observations.py"
    spec = importlib.util.spec_from_file_location(PREFIX + ".routers.observations", path)
    route = importlib.util.module_from_spec(spec); sys.modules[spec.name] = route; spec.loader.exec_module(route)
    return route, PaginationParams


class Database:
    def __init__(self, connection): self.connection = connection; self.calls = []
    async def execute(self, statement, params):
        self.calls.append((statement, dict(params)))
        return self.connection.execute(statement, params)


BOXES = {
    "san_diego": (-117.3, 32.6, -117, 32.9),
    "zero_positive": (0, 0, 1, 1), "zero_negative": (-1, -1, 0, 0),
    "equator": (-20, -1, 20, 1), "prime_meridian": (-1, -30, 1, 30),
    "north_pole": (-180, 89, 180, 90), "south_pole": (-180, -90, 180, -89),
    "east_dateline": (179, -90, 180, 90), "west_dateline": (-180, -90, -179, 90),
    "width_180": (-90, -45, 90, 45), "width_over_180": (-170, -60, 170, 60),
    "whole_world": (-180, -90, 180, 90), "whole_longitude": (-180, -20, 180, 20),
    "hemisphere_north": (-180, 0, 180, 90), "hemisphere_south": (-180, -90, 180, 0),
    "narrow_polar": (20, 89, 40, 90), "boundary_square": (-1, -1, 1, 1),
}
ORACLE = """COALESCE(ST_X(o.location::geometry), (o.metadata->>'longitude')::double precision)
 BETWEEN :min_lon AND :max_lon AND
 COALESCE(ST_Y(o.location::geometry), (o.metadata->>'latitude')::double precision)
 BETWEEN :min_lat AND :max_lat"""


class Viewport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = os.environ.get("MAP_FIXTURE_DSN")
        if not dsn:
            raise unittest.SkipTest("MAP_FIXTURE_DSN required; no implicit database")
        url = make_url(dsn)
        if url.database != "map_fixture" or url.drivername not in ("postgresql+psycopg", "postgresql+psycopg2"):
            raise ValueError("Only explicit dedicated map_fixture PostgreSQL database is permitted")
        cls.engine = create_engine(dsn)
        cls.route, cls.pagination = load_route()
        cls.receipts = []
        with cls.engine.begin() as db:
            db.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            db.execute(text("CREATE SCHEMA IF NOT EXISTS core")); db.execute(text("CREATE SCHEMA IF NOT EXISTS obs"))
            db.execute(text("""CREATE TABLE IF NOT EXISTS core.taxon (
              id uuid PRIMARY KEY, canonical_name text, common_name text, kingdom text, fungi_type text)"""))
            db.execute(text("""CREATE TABLE IF NOT EXISTS obs.observation (
              id uuid PRIMARY KEY, taxon_id uuid REFERENCES core.taxon(id), source text NOT NULL,
              source_id text, observer text, observed_at timestamptz NOT NULL, location geography(Point,4326),
              accuracy_m double precision, media jsonb NOT NULL DEFAULT '[]', notes text,
              metadata jsonb NOT NULL DEFAULT '{}')"""))
            db.execute(text("TRUNCATE obs.observation, core.taxon"))
            db.execute(text("CREATE INDEX IF NOT EXISTS idx_observation_location ON obs.observation USING gist(location)"))
            db.execute(text("CREATE INDEX IF NOT EXISTS idx_observation_observed_at ON obs.observation(observed_at DESC)"))
            for i, kingdom in [(1, "Fungi"), (2, "Plantae")]:
                db.execute(text("INSERT INTO core.taxon VALUES (:id, :name, :name, :kingdom, NULL)"),
                           {"id": str(UUID(int=i)), "name": "synthetic " + kingdom, "kingdom": kingdom})
            longitudes = [-180, -179.5, -170, -90, -117.3, -117.15, -117, -20, -1, 0, 1, 20, 30, 40, 90, 170, 179.5, 180]
            latitudes = [-90, -89.5, -89, -60, -45, -30, -20, -1, 0, 1, 20, 30, 32.6, 32.75, 32.9, 45, 60, 89, 89.5, 90]
            points = list(itertools.product(longitudes, latitudes))
            points += [(math.nextafter(x, d), y) for x, y in [(-1, 0), (1, 0), (-117.3, 32.75), (-117, 32.75)] for d in [-math.inf, math.inf]]
            points += [(x, math.nextafter(y, d)) for x, y in [(0, -1), (0, 1), (-117.15, 32.6), (-117.15, 32.9)] for d in [-math.inf, math.inf]]
            points += [(-117.15, 32.90005)]
            cls.point_count = len(points)
            for i, (lon, lat) in enumerate(points, 1):
                db.execute(text("""INSERT INTO obs.observation(id,taxon_id,source,source_id,observed_at,location)
                 VALUES (:id,:taxon,'viewport_fixture',:source_id,TIMESTAMPTZ '2026-01-01' + :seconds * INTERVAL '1 second',
                 ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography)"""),
                 {"id": str(UUID(int=100+i)), "taxon": str(UUID(int=1 if i % 2 else 2)), "source_id": str(i), "seconds": i, "lon": lon, "lat": lat})
            for i, metadata in enumerate([{"longitude":0,"latitude":0}, {"longitude":180,"latitude":90}, {}, {"longitude":-117.1,"latitude":32.7}], 1):
                db.execute(text("""INSERT INTO obs.observation(id,taxon_id,source,observed_at,metadata)
                 VALUES (:id,:taxon,'metadata_fixture',TIMESTAMPTZ '2026-01-02' + :i * INTERVAL '1 second',CAST(:metadata AS jsonb))"""),
                 {"id":str(UUID(int=10000+i)),"taxon":str(UUID(int=1)),"i":i,"metadata":json.dumps(metadata)})
            db.execute(text("ANALYZE obs.observation"))
            cls.version = db.execute(text("SELECT version(), postgis_full_version()")).one()._asdict()

    @classmethod
    def tearDownClass(cls):
        out = os.environ.get("MAP_FIXTURE_RECEIPT")
        if out:
            Path(out).write_text(json.dumps({"scope":"native synthetic PostgreSQL/PostGIS; no production data",
                "source_sha256":hashlib.sha256((ROOT/'mindex_api/routers/observations.py').read_bytes()).hexdigest(),
                "version":cls.version, "point_count":cls.point_count,"cases":cls.receipts}, indent=2, default=str)+"\n")
        cls.engine.dispose()

    def call(self, db, bbox, limit=1000, offset=0, include_total=True, **filters):
        args = dict(pagination=self.pagination(limit=limit,offset=offset), db=db, bbox=bbox,
                    include_total=include_total,taxon_id=None,kingdom=None,start=None,end=None)
        args.update(filters)
        return asyncio.run(self.route.list_observations(**args))

    def compare(self, name, **filters):
        box = BOXES[name]; params = dict(zip(('min_lon','min_lat','max_lon','max_lat'),box))
        clauses = [ORACLE]
        if filters.get('taxon_id'):
            clauses.append('o.taxon_id = :taxon'); params['taxon'] = str(filters['taxon_id'])
        if filters.get('kingdom'):
            clauses.append('lower(t.kingdom) = :kingdom'); params['kingdom'] = filters['kingdom'].lower()
        if filters.get('start'):
            clauses.append('o.observed_at >= :start');params['start']=filters['start']
        if filters.get('end'):
            clauses.append('o.observed_at <= :end');params['end']=filters['end']
        with self.engine.connect() as connection:
            connection.execute(text('SET statement_timeout=5000'))
            expected = list(connection.execute(text('SELECT o.id FROM obs.observation o LEFT JOIN core.taxon t ON t.id=o.taxon_id WHERE '+ ' AND '.join(clauses)+' ORDER BY o.observed_at DESC'), params).scalars())
            db = Database(connection); started = time.perf_counter()
            result = self.call(db, ','.join(map(str,box)), **filters)
            actual = [x.id for x in result.data]
            self.receipts.append({'case':self._testMethodName,'bbox':box,'matched':actual==expected,
                                  'expected_count':len(expected),'actual_count':len(actual),'total':result.pagination.total,
                                  'missing_ids':[str(x) for x in expected if x not in actual],
                                  'extra_ids':[str(x) for x in actual if x not in expected],
                                  'wall_seconds':time.perf_counter()-started})
            self.assertEqual(actual, expected)
            self.assertEqual(result.pagination.total, len(expected))
            self.assertEqual(len(db.calls), 2)

    def test_filters_with_bbox(self):
        self.compare('whole_world', taxon_id=UUID(int=1), kingdom='fungi',
                     start=datetime(2026,1,1,0,1,tzinfo=timezone.utc), end=datetime(2026,1,1,0,4,tzinfo=timezone.utc))

    def test_pagination_count_metadata_and_optional_count(self):
        with self.engine.connect() as connection:
            db = Database(connection)
            full = self.call(db, '-180,-90,180,90')
            page = self.call(db, '-180,-90,180,90', limit=3, offset=2)
            self.assertEqual([x.id for x in page.data], [x.id for x in full.data][2:5])
            self.assertEqual(page.pagination.total, full.pagination.total)
            before = len(db.calls)
            no_count = self.call(db, '0,0,1,1', include_total=False)
            self.assertEqual(len(db.calls)-before, 1); self.assertIsNone(no_count.pagination.total)
            row = next(x for x in no_count.data if x.source == 'metadata_fixture')
            self.assertIsNone(row.location)  # Preserve existing projection, not a new fallback response contract.
            self.assertEqual(row.metadata['latitude'], 0)
            self.assertEqual(row.metadata['taxon_name'], 'synthetic Fungi')

    def test_invalid_bbox_never_queries(self):
        from fastapi import HTTPException
        with self.engine.connect() as connection:
            db=Database(connection)
            for box in ('170,-10,-170,10','nan,0,1,1','-181,0,1,1'):
                with self.assertRaises(HTTPException): self.call(db,box)
            self.assertEqual(db.calls,[])


for _name in BOXES:
    setattr(Viewport,'test_rectangle_'+_name,lambda self,name=_name:self.compare(name))

def benchmark():
    """Bounded synthetic experiment; exact expression index only in map_fixture.

    This deliberately rebuilds the isolated fixture, then adds 100,000 synthetic
    points. It neither sizes nor modifies a production database.
    """
    Viewport.setUpClass()
    engine = Viewport.engine
    try:
        with engine.begin() as db:
            db.execute(text("DROP INDEX IF EXISTS obs.idx_observation_location_geometry"))
            db.execute(text("""INSERT INTO obs.observation(id,taxon_id,source,observed_at,location)
              SELECT md5('viewport-benchmark-' || i)::uuid,
              CASE WHEN i % 2=0 THEN '00000000-0000-0000-0000-000000000001'::uuid
                   ELSE '00000000-0000-0000-0000-000000000002'::uuid END,
              'synthetic_benchmark',TIMESTAMPTZ '2026-02-01' + i * INTERVAL '1 second',
              ST_SetSRID(ST_MakePoint(
                CASE WHEN i%5=0 THEN -117.3 + (i%997)*0.3/997 ELSE -180+(i%3599)*0.1 END,
                CASE WHEN i%5=0 THEN 32.6 + (i%991)*0.3/991 ELSE -90+(i%1799)*0.1 END),4326)::geography
              FROM generate_series(1,100000) i"""))
            db.execute(text('ANALYZE obs.observation'))
        class Capture:
            def __init__(self): self.calls=[]
            async def execute(self, statement, params): self.calls.append((statement,params));return self
            def mappings(self):return self
            def all(self):return []
            def scalar_one(self):return 0
        queries=[]
        for name in ('san_diego','zero_positive','whole_world'):
            db=Capture()
            asyncio.run(Viewport.route.list_observations(pagination=Viewport.pagination(limit=2,offset=0),
                db=db,bbox=','.join(map(str,BOXES[name])),include_total=True,taxon_id=None,kingdom=None,start=None,end=None))
            for kind,(statement,params) in zip(('data','count'),db.calls):
                queries.append((name+'_'+kind,str(statement),params))
        rows=[]
        ddl = 'CREATE INDEX CONCURRENTLY idx_observation_location_geometry ON obs.observation USING gist ((location::geometry)) WHERE location IS NOT NULL'
        for phase in ('without_expression_index','with_expression_index','indexed_with_expression_statistics','indexed_custom_plan_diagnostic'):
            if phase=='with_expression_index':
                started=time.perf_counter()
                with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as db:
                    db.execute(text(ddl))
                    index_bytes=db.execute(text("SELECT pg_relation_size('obs.idx_observation_location_geometry')")).scalar_one()
                index_seconds=time.perf_counter()-started
            if phase=='indexed_with_expression_statistics':
                with engine.begin() as db:
                    db.execute(text('ANALYZE obs.observation'))
            for name,sql,params in queries:
                for iteration in (1,2):
                    with engine.connect() as db:
                        db.execute(text('SET statement_timeout=5000'))
                        if phase=='indexed_custom_plan_diagnostic':
                            db.execute(text('SET LOCAL plan_cache_mode=force_custom_plan'))
                        cache_mode=db.execute(text('SHOW plan_cache_mode')).scalar_one()
                        started=time.perf_counter()
                        try:
                            plan=db.execute(text('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql),params).scalar_one()
                            row={'phase':phase,'case':name,'iteration':iteration,'ok':True,'plan':plan,
                                 'plan_cache_mode':cache_mode,
                                 'prepared_plan_counters':[dict(x) for x in db.execute(text('SELECT name,generic_plans,custom_plans FROM pg_prepared_statements')).mappings()]}
                        except Exception as error:
                            row={'phase':phase,'case':name,'iteration':iteration,'ok':False,
                                 'error_type':type(error).__name__,'sqlstate':getattr(getattr(error,'orig',None),'sqlstate',None)}
                        row['wall_seconds']=time.perf_counter()-started;rows.append(row)
        out=Path(os.environ['MAP_FIXTURE_RECEIPT'])
        out.write_text(json.dumps({'scope':'synthetic native PostGIS expression-index experiment only',
            'source_sha256':hashlib.sha256((ROOT/'mindex_api/routers/observations.py').read_bytes()).hexdigest(),
            'version':Viewport.version,'synthetic_rows':100000+Viewport.point_count+4,
            'index_ddl':ddl,'index_bytes':index_bytes,'index_build_wall_seconds':index_seconds,
            'statement_timeout_ms':5000,'cases':rows},indent=2,default=str)+'\n')
        print(json.dumps({'benchmark_rows':len(rows),'failed_queries':sum(not row['ok'] for row in rows),
                          'index_bytes':index_bytes}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    if sys.argv[1:] == ['--benchmark']:
        benchmark()
    else:
        unittest.main(verbosity=2)
