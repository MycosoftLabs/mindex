"""Real temporary SQLite/SQLAlchemy transactions; fixture dialect translation only.

The actual route/DTO/helper bodies execute without application startup. A narrow
adapter translates PostgreSQL UUID/JSON/geography expressions for SQLite and
delegates transaction operations to a real SQLAlchemy Session. This does not
validate PostgreSQL/PostGIS syntax, async driver behavior or production schemas.
"""
import ast
from contextlib import closing
import itertools
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import List, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mindex_api/routers/observations.py"
FIXTURE_TIME = "2026-09-29T12:00:00+00:00"


def load_contract(events):
    names = {"BulkObservationItem", "BulkIngestRequest", "BulkIngestResponse",
             "_kingdom_from_iconic", "_upsert_bulk_observation", "bulk_ingest_observations"}
    selected = []
    for node in ast.parse(SOURCE.read_text(encoding="utf-8-sig"), str(SOURCE)).body:
        if getattr(node, "name", None) in names:
            node.decorator_list = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
                node.args.kw_defaults = [ast.Constant(None) if item is not None else None for item in node.args.kw_defaults]
            selected.append(node)
    ids = itertools.count(1)
    env = dict(BaseModel=BaseModel, Field=Field, Optional=Optional, List=List,
               HTTPException=HTTPException, status=status, json=json, text=text,
               logger=logging.getLogger("batch6_fixture"),
               uuid4=lambda: f"fixture-insert-{next(ids)}",
               schedule_domain_event=lambda **payload: events.append(payload))
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), env)
    for name in ("BulkObservationItem", "BulkIngestRequest", "BulkIngestResponse"):
        env[name].model_rebuild(_types_namespace=env)
    return env


def finish(coroutine):
    """All fixture adapters are synchronous underneath; no event loop/socket."""
    try:
        yielded = coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    coroutine.close()
    raise AssertionError(f"Unexpected asynchronous fixture yield: {yielded!r}")


def translate(sql):
    """Only a test dialect seam; transaction/control-flow code is unmodified."""
    sql = str(sql).replace("core.taxon", "taxon").replace("obs.observation", "observation")
    sql = re.sub(r"COALESCE\(metadata, '\{\}'::jsonb\) \|\| CAST\(:(metadata|meta) AS jsonb\)",
                 lambda match: f"json_patch(COALESCE(metadata, '{{}}'), :{match.group(1)})", sql, flags=re.I)
    sql = re.sub(r"CAST\((:[a-z_]+) AS jsonb\)", r"\1", sql, flags=re.I)
    sql = re.sub(r"\s+AS uuid\)", " AS TEXT)", sql, flags=re.I)
    sql = re.sub(r"::(?:jsonb|timestamptz|geography)", "", sql, flags=re.I)
    return sql


def snapshot(path):
    # Independent connection: no uncommitted state from the writer can leak in.
    with closing(sqlite3.connect(path)) as reader:
        reader.row_factory = sqlite3.Row
        return {
            "observations": [dict(row) for row in reader.execute(
                "SELECT source_id,taxon_id,notes,observer,observed_at,metadata FROM observation ORDER BY source_id")],
            "taxa": [dict(row) for row in reader.execute(
                "SELECT id,canonical_name,common_name,metadata FROM taxon ORDER BY canonical_name")],
        }


class AsyncSavepoint:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.transaction = self.session.begin_nested()
        self.transaction.__enter__()
        return self

    async def __aexit__(self, *exception):
        return self.transaction.__exit__(*exception)


class AsyncFixtureSession:
    def __init__(self, engine, path, events):
        self.session = Session(engine)
        self.path = path
        self.events = events
        self.precommit = []
        self.commit_failures = 0

    def begin_nested(self):
        return AsyncSavepoint(self.session)

    async def execute(self, sql, params=None):
        return self.session.execute(text(translate(sql)), params or {})

    async def commit(self):
        self.precommit.append(snapshot(self.path))
        assert not self.events, "Completion event emitted before outer commit"
        try:
            self.session.commit()
        except Exception:
            self.commit_failures += 1
            raise

    async def rollback(self):
        self.session.rollback()


@pytest.fixture
def fixture_db(tmp_path):
    path = tmp_path / "explicit-fixture.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    controls = []
    taxon_ids = itertools.count(1)

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        # Required: SQLite legacy SAVEPOINT release must not commit the batch.
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("NOW", 0, lambda: FIXTURE_TIME)
        connection.create_function("fixture_id", 0, lambda: f"fixture-taxon-{next(taxon_ids)}")
        connection.create_function("ST_MakePoint", 2, lambda lng, lat: json.dumps([lng, lat]))
        connection.create_function("ST_SetSRID", 2, lambda point, _: point)

    @event.listens_for(engine, "begin")
    def explicit_begin(connection):
        connection.exec_driver_sql("BEGIN")

    @event.listens_for(engine, "before_cursor_execute")
    def record_control(_, __, statement, ___, ____, _____):
        if statement.upper().startswith(("BEGIN", "SAVEPOINT", "RELEASE", "ROLLBACK")):
            controls.append(statement)

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE fixture_observer(login TEXT PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO fixture_observer VALUES ('fixture-observer')")
        connection.exec_driver_sql("""CREATE TABLE taxon(
            id TEXT PRIMARY KEY DEFAULT (fixture_id()), canonical_name TEXT NOT NULL,
            rank TEXT, common_name TEXT, source TEXT, kingdom TEXT, metadata TEXT,
            updated_at TEXT DEFAULT 'fixture-original-time')""")
        connection.exec_driver_sql("""CREATE TABLE observation(
            id TEXT PRIMARY KEY, taxon_id TEXT REFERENCES taxon(id), source TEXT NOT NULL,
            source_id TEXT, observed_at TEXT NOT NULL,
            observer TEXT REFERENCES fixture_observer(login) DEFERRABLE INITIALLY DEFERRED,
            location TEXT, media TEXT, notes TEXT, metadata TEXT,
            UNIQUE(source,source_id), CHECK(notes IS NULL OR notes <> 'fixture-rejected-row'))""")
    events = []
    api = load_contract(events)
    db = AsyncFixtureSession(engine, path, events)
    bundle = dict(path=path, engine=engine, controls=controls, events=events, api=api, db=db)
    yield bundle
    db.session.close()
    engine.dispose()


def item(api, key, *, rejected=False, observer="fixture-observer", notes=None):
    return api["BulkObservationItem"](
        source="fixture", source_id=key, observed_at=FIXTURE_TIME, observer=observer,
        taxon_name=f"Fixture taxon {key}", taxon_common_name=f"Fixture revised {key}",
        iconic_taxon_name="Fungi", notes="fixture-rejected-row" if rejected else notes or f"fixture-revised-{key}",
        metadata={"fixture": True, "revision": 2},
    )


def seed(bundle, keys):
    with bundle["engine"].begin() as connection:
        for key in keys:
            connection.execute(text("INSERT INTO taxon(id,canonical_name,common_name,metadata) VALUES (:id,:name,:common,:metadata)"),
                               {"id":f"fixture-existing-taxon-{key}", "name":f"Fixture taxon {key}", "common":f"Fixture original {key}", "metadata":'{"fixture":true,"revision":1}'})
            connection.execute(text("INSERT INTO observation(id,taxon_id,source,source_id,observed_at,observer,notes,metadata) VALUES (:id,:taxon,'fixture',:key,:time,'fixture-observer',:notes,:metadata)"),
                               {"id":f"fixture-existing-observation-{key}","taxon":f"fixture-existing-taxon-{key}","key":key,"time":"2026-01-01T00:00:00+00:00","notes":f"fixture-original-{key}","metadata":'{"fixture":true,"revision":1}'})


def run(bundle, rows):
    return finish(bundle["api"]["bulk_ingest_observations"](
        bundle["api"]["BulkIngestRequest"](observations=rows), bundle["db"]))


def receipt(bundle, label, response=None, error=None):
    evidence = {
        "case":label, "response":response.model_dump() if response else None,
        "error_type":type(error).__name__ if error else None,
        "error_status":getattr(error,"status_code",None),
        "committed":snapshot(bundle["path"]), "precommit":bundle["db"].precommit,
        "event_contexts":[event["context"] for event in bundle["events"]],
        "session_transaction_open":bundle["db"].session.in_transaction(),
        "control_sql":bundle["controls"], "sqlite_engine":"real owned temporary fixture",
    }
    evidence_path = os.getenv("BATCH6_BULK_EVIDENCE")
    if evidence_path:
        with Path(evidence_path).open("a", encoding="utf-8") as file:
            file.write(json.dumps(evidence,sort_keys=True)+"\n")
    return evidence


def assert_counts(bundle, response, inserted, skipped, errors):
    assert response.model_dump() == dict(inserted=inserted,skipped=skipped,errors=errors)
    assert len(bundle["events"]) == 1
    context = bundle["events"][0]["context"]
    assert {key:context[key] for key in ("inserted","skipped","errors")} == response.model_dump()
    assert not bundle["db"].session.in_transaction()


@pytest.mark.parametrize("failure_index", [0,1,2], ids=["first","middle","last"])
def test_insert_partial_failure_commits_only_successful_rows_and_taxa(fixture_db, failure_index):
    bundle=fixture_db
    initial=snapshot(bundle["path"])
    rows=[item(bundle["api"],f"row-{index}",rejected=index==failure_index) for index in range(3)]
    response=run(bundle,rows)
    result=receipt(bundle,f"insert-failure-{failure_index}",response)
    expected=[f"row-{index}" for index in range(3) if index!=failure_index]
    assert [row["source_id"] for row in result["committed"]["observations"]] == expected
    assert [row["canonical_name"] for row in result["committed"]["taxa"]] == [f"Fixture taxon {key}" for key in expected]
    assert bundle["db"].precommit == [initial]
    assert_counts(bundle,response,2,0,1)


@pytest.mark.parametrize("failure_index", [0,1,2], ids=["first","middle","last"])
def test_update_partial_failure_restores_failed_observation_and_taxon(fixture_db, failure_index):
    bundle=fixture_db
    keys=[f"row-{index}" for index in range(3)]
    seed(bundle,keys)
    initial=snapshot(bundle["path"])
    response=run(bundle,[item(bundle["api"],key,rejected=index==failure_index) for index,key in enumerate(keys)])
    result=receipt(bundle,f"update-failure-{failure_index}",response)
    for index,row in enumerate(result["committed"]["observations"]):
        assert row["notes"] == (f"fixture-original-row-{index}" if index==failure_index else f"fixture-revised-row-{index}")
    for index,row in enumerate(result["committed"]["taxa"]):
        assert row["common_name"] == (f"Fixture original row-{index}" if index==failure_index else f"Fixture revised row-{index}")
    assert bundle["db"].precommit == [initial]
    assert_counts(bundle,response,0,2,1)


def test_mixed_success_preserves_response_shape_and_duplicate_update_meaning(fixture_db):
    bundle=fixture_db
    seed(bundle,["existing"])
    initial=snapshot(bundle["path"])
    response=run(bundle,[item(bundle["api"],"new"),item(bundle["api"],"existing"),item(bundle["api"],"new",notes="fixture-second-version")])
    result=receipt(bundle,"mixed-success",response)
    assert len(result["committed"]["observations"]) == 2
    assert len(result["committed"]["taxa"]) == 2
    assert result["committed"]["observations"][1]["notes"] == "fixture-second-version"
    assert bundle["db"].precommit == [initial]
    assert_counts(bundle,response,1,2,0)


@pytest.mark.parametrize("mode", ["all-rejected","missing-source-id","empty"])
def test_no_successful_rows_produces_no_persisted_taxon_or_observation(fixture_db, mode):
    bundle=fixture_db
    rows=([item(bundle["api"],f"row-{index}",rejected=True) for index in range(3)] if mode=="all-rejected"
          else [item(bundle["api"],None)] if mode=="missing-source-id" else [])
    response=run(bundle,rows)
    result=receipt(bundle,mode,response)
    assert result["committed"] == {"observations":[],"taxa":[]}
    assert_counts(bundle,response,0,0,len(rows))


@pytest.mark.parametrize("mode", ["insert","update","mixed"])
def test_outer_commit_constraint_failure_returns_no_success_or_event_and_rolls_back(fixture_db, mode):
    bundle=fixture_db
    if mode in ("update","mixed"):
        seed(bundle,["existing"])
    initial=snapshot(bundle["path"])
    failing=item(bundle["api"],"existing" if mode!="insert" else "new",observer="fixture-missing-deferred-parent")
    rows=[item(bundle["api"],"good-new"),failing] if mode=="mixed" else [failing]
    error=None
    try:
        run(bundle,rows)
    except Exception as exception:
        error=exception
    result=receipt(bundle,f"commit-failure-{mode}",error=error)
    assert bundle["db"].commit_failures == 1, "Fixture must fail actual outer COMMIT"
    assert isinstance(error,HTTPException) and error.status_code == 503
    assert result["committed"] == initial
    assert bundle["db"].precommit == [initial]
    assert bundle["events"] == []
    assert not bundle["db"].session.in_transaction()
