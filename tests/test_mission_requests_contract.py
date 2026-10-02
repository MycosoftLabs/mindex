"""Offline real-router/auth/repository contract tests. No PostgreSQL or Stripe calls."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sys
import time
import types
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
PKG = "mission_contract_fixture"
for suffix in ("", ".auth", ".routers"):
    package = types.ModuleType(PKG + suffix); package.__path__ = []
    sys.modules[package.__name__] = package
def load(name, relative):
    spec = importlib.util.spec_from_file_location(PKG + "." + name, ROOT / relative)
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    spec.loader.exec_module(module); return module
configuration = types.ModuleType(PKG + ".config")
configuration.settings = types.SimpleNamespace(internal_tokens=["fixture-internal"], api_keys=[], internal_auth_secret=None)
sys.modules[configuration.__name__] = configuration
database = types.ModuleType(PKG + ".db")
def forbidden_db(): raise AssertionError("Must not connect to a real database")
database.async_session_scope = forbidden_db; sys.modules[database.__name__] = database
load("auth.models", "mindex_api/auth/models.py")
auth = load("auth.internal_auth", "mindex_api/auth/internal_auth.py")
sys.modules[PKG + ".auth"].require_internal_token = auth.require_internal_token
core = load("mission_requests", "mindex_api/mission_requests.py")
routes = load("routers.mission_requests", "mindex_api/routers/mission_requests.py")
ID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
ISSUER = "https://identity.test/auth/v1"
SECRET = "fixture-only-delegation-secret-32-characters"
BODY = dict(purpose="Study soil moisture at the site", task="Measure conditions across this area", deliverables="Georeferenced observations", location_label="Study site", latitude=32, longitude=-117, radius_m=100, access_notes="", privacy_consent=True)
PATH = "/api/mindex/internal/mission-requests"

@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setenv("MISSION_REQUESTS_ENABLED", "1")
    monkeypatch.setenv("MISSION_REQUEST_DELEGATION_SECRET", SECRET)
    monkeypatch.setenv("MISSION_REQUEST_IDENTITY_ISSUER", ISSUER)

def signed(raw=b"", path=PATH, method="POST", role="user", subject=ID, timestamp=None):
    timestamp = str(timestamp if timestamp is not None else int(time.time()))
    text = "\n".join([timestamp, method, path, role, ISSUER, subject, hashlib.sha256(raw).hexdigest()])
    return {"x-internal-token":"fixture-internal", "x-mission-timestamp":timestamp, "x-mission-subject":subject,
            "x-mission-issuer":ISSUER, "x-mission-role":role, "x-mission-signature":hmac.new(SECRET.encode(),text.encode(),hashlib.sha256).hexdigest()}

@pytest.mark.parametrize("patch", [{"latitude": "32"}, {"latitude": True}, {"longitude": float("inf")}, {"radius_m":1.5}, {"privacy_consent":False}, {"owner":OTHER}])
def test_strict_mission_fields(patch):
    with pytest.raises(ValidationError): core.MissionInput.model_validate({**BODY, **patch})

def test_signature_binds_entire_envelope():
    raw = '{"purpose":"\u00e9tude"}'.encode()
    headers = signed(raw, timestamp=1800000000)
    assert headers["x-mission-signature"] == "074b7ef9d12a8b5cd087f0fed75e7864d16a648e1e1e6658fcf300af810acc11"
    assert core.verify_delegation(headers,"POST",PATH,raw,now=1800000000).subject == ID
    for method,path,payload in [("GET",PATH,raw),("POST",PATH+"/other",raw),("POST",PATH,b"{}")]:
        with pytest.raises(HTTPException) as failure: core.verify_delegation(headers,method,path,payload,now=1800000000)
        assert failure.value.status_code == 401

def test_expired_delegation_rejected():
    with pytest.raises(HTTPException) as failure: core.verify_delegation(signed(timestamp=100),"POST",PATH,b"",now=161)
    assert failure.value.status_code == 401

def event(kind="paid", **kwargs):
    return core.PaymentEvent(event_id="evt_fixture", event_type=kind, amount_minor=1000, currency="usd", payment_intent="pi_fixture", **kwargs)

@pytest.mark.parametrize("state,kind,refund,expected", [("checkout","paid",0,"paid"),("paid","failed",0,"paid"),("paid","expired",0,"paid"),("paid","refund",1000,"refunded"),("paid","refund",300,"partially_refunded"),("refunded","paid",0,"refunded")])
def test_payment_never_means_dispatch_and_late_events_preserve_payment(state,kind,refund,expected):
    result = core.payment_transition(state,event(kind,refunded_minor=refund),1000,"usd",1000 if state=="refunded" else 0)
    assert result[0] == expected
    assert result[0] not in ("queued","scheduled","dispatched")

def test_refunds_monotonic_and_quote_checked():
    assert core.payment_transition("partially_refunded",event("refund",refunded_minor=100),1000,"usd",300) == ("partially_refunded",300)
    with pytest.raises(HTTPException): core.payment_transition("checkout",event(),999,"usd")
    with pytest.raises(HTTPException): core.payment_transition("paid",event("refund",refunded_minor=1001),1000,"usd")

class Repo:
    def __init__(self): self.calls=[]; self.fail=False
    async def create(self,principal,key,mission):
        self.calls.append((principal,key,mission))
        if self.fail: raise RuntimeError("db offline")
        return {"id":ID,"retention":"postgres_committed"}
    async def get(self,principal,request_id):
        self.calls.append((principal,request_id))
        if principal.subject != ID: core.fail("mission_not_found",404)
        return {"id":ID,"mission_status":"not_scheduled"}
    async def reserve_checkout(self,*args): self.calls.append(args); core.fail("quote_pending_review",409)
    async def apply_event(self,*args): self.calls.append(args); return {"status":"recorded","mission_status":"not_scheduled"}

def call(path=PATH,method="POST",raw=None,headers=None,repo=None):
    raw = json.dumps({"idempotency_key":ID,"mission":BODY}).encode() if raw is None else raw
    repo=repo or Repo(); app=FastAPI(); app.include_router(routes.router,prefix="/api/mindex/internal")
    app.dependency_overrides[routes.repository]=lambda:repo
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://fixture") as client:
            return await client.request(method,path,content=raw,headers=headers if headers is not None else signed(raw,path,method))
    return asyncio.run(invoke()),repo

def test_actual_route_requires_service_and_owner_auth():
    response,repo=call(headers={}); assert response.status_code==401; assert not repo.calls
    raw=json.dumps({"idempotency_key":ID,"mission":BODY}).encode(); headers=signed(raw); headers["x-mission-subject"]=OTHER
    response,repo=call(raw=raw,headers=headers); assert response.status_code==401; assert not repo.calls

def test_real_route_returns_commit_only_and_private_response():
    response,repo=call(); assert response.status_code==201; assert response.json()["retention"]=="postgres_committed"
    assert response.headers["cache-control"]=="private, no-store"; assert repo.calls[0][0].subject==ID

def test_storage_failure_is_503_not_saved():
    repo=Repo();repo.fail=True; response,_=call(repo=repo); assert response.status_code==503; assert "id" not in response.json()

def test_other_owner_read_is_not_found():
    path=PATH+"/"+ID
    response,_=call(path,"GET",b"",signed(b"",path,"GET",subject=OTHER)); assert response.status_code==404

def test_client_quote_amount_rejected_before_repo():
    path=PATH+"/"+ID+"/checkout"; raw=b'{"amount_minor":1}'
    response,repo=call(path,raw=raw); assert response.status_code==422; assert not repo.calls

def test_quote_missing_does_not_queue():
    response,_=call(PATH+"/"+ID+"/checkout",raw=b"{}"); assert response.status_code==409
    assert response.json()["detail"]["code"]=="quote_pending_review"

def test_user_delegation_cannot_invoke_webhook():
    response,repo=call(PATH+"/events/stripe",raw=b"{}"); assert response.status_code==401; assert not repo.calls

def test_signed_oversized_request_rejected():
    response,repo=call(raw=b"x"*17000); assert response.status_code==413; assert not repo.calls

class Result:
    def __init__(self,row=None,scalar=0): self.row=row; self.scalar=scalar
    def mappings(self): return self
    def first(self): return self.row
    def scalar_one(self): return self.scalar

class Database:
    def __init__(self,existing=None,count=0,fail_commit=False): self.existing=existing;self.count=count;self.fail_commit=fail_commit;self.committed=False;self.sql=[]
    @asynccontextmanager
    async def begin(self):
        yield self
        if self.fail_commit: raise RuntimeError("commit failed")
        self.committed=True
    async def execute(self,statement,params):
        self.sql.append((str(statement),params))
        if "payload_sha256 FROM" in str(statement): return Result(self.existing)
        if "count(*)" in str(statement): return Result(scalar=self.count)
        return Result()
    @asynccontextmanager
    async def session(self): yield self

def test_repository_cannot_return_receipt_before_commit():
    db=Database(fail_commit=True);repo=core.MissionRepository(db.session)
    with pytest.raises(RuntimeError): asyncio.run(repo.create(core.Principal(ISSUER,ID),UUID(ID),core.MissionInput(**BODY)))
    assert not db.committed

def test_repository_idempotency_conflict_and_rate_limit():
    for db,status in [(Database(existing={"id":ID,"payload_sha256":"different"}),409),(Database(count=10),429)]:
        with pytest.raises(HTTPException) as failure: asyncio.run(core.MissionRepository(db.session).create(core.Principal(ISSUER,ID),UUID(ID),core.MissionInput(**BODY)))
        assert failure.value.status_code==status
        assert not any("INSERT" in sql for sql,_ in db.sql)

def test_identical_retry_precedes_rate_admission_and_uses_owner_predicate():
    mission=core.MissionInput(**BODY);db=Database(existing={"id":ID,"payload_sha256":core.digest(mission.model_dump())},count=100)
    result=asyncio.run(core.MissionRepository(db.session).create(core.Principal(ISSUER,ID),UUID(ID),mission))
    assert result["duplicate"] and db.committed
    query,params=db.sql[1];assert "issuer=:issuer" in query and "owner_subject=" in query
    assert params["owner"]==ID and params["issuer"]==ISSUER

@pytest.mark.parametrize("same", [True, False])
def test_repository_event_deduplication_never_reapplies_transition(same):
    incoming=event()
    class EventDatabase(Database):
        async def execute(self,statement,params):
            self.sql.append((str(statement),params))
            if "FROM mission.payment_event" in str(statement):
                return Result({"payload_sha256":core.digest(incoming.model_dump(mode="json")) if same else "different"})
            return Result()
    db=EventDatabase();repo=core.MissionRepository(db.session)
    if same:
        assert asyncio.run(repo.apply_event(incoming))=={"status":"duplicate"}
        assert db.committed
    else:
        with pytest.raises(HTTPException) as failure: asyncio.run(repo.apply_event(incoming))
        assert failure.value.status_code==409
    assert not any("UPDATE" in sql or "INSERT" in sql for sql,_ in db.sql)

def test_missing_payment_intent_cannot_record_paid():
    db=Database();repo=core.MissionRepository(db.session)
    incoming=core.PaymentEvent(event_id="evt_missing",event_type="paid",amount_minor=1000,currency="usd")
    with pytest.raises(HTTPException) as failure: asyncio.run(repo.apply_event(incoming))
    assert failure.value.status_code==422 and not db.sql
