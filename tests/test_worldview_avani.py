from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from mindex_api.routers.worldview.avani_gateway import review_worldview_response
from mindex_api.routers.worldview.response_envelope import wrap_governed_response, wrap_response


@dataclass
class DummyCaller:
    key_id: uuid.UUID = field(default_factory=uuid.uuid4)
    owner_id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_type: str = "agent"
    plan: str = "pro"
    scopes: list[str] = field(default_factory=lambda: ["worldview:read"])
    service: str = "worldview"


def _caller() -> DummyCaller:
    return DummyCaller()


def test_wrap_response_keeps_data_and_adds_optional_avani_meta():
    response = wrap_response(
        data=[{"id": "one"}],
        avani={"avani_verdict": "allow", "audit_trail_id": "avani-test"},
    )

    assert response["data"] == [{"id": "one"}]
    assert response["meta"]["avani"]["avani_verdict"] == "allow"


@pytest.mark.asyncio
async def test_degraded_local_review_denies_internal_domains(monkeypatch):
    monkeypatch.setattr("mindex_api.routers.worldview.avani_gateway.settings.avani_api_url", None)

    review = await review_worldview_response(
        worldview_request_id="req-1",
        data={"results": []},
        source_domains=["telemetry"],
        caller=_caller(),
    )

    assert review["avani_verdict"] == "deny"
    assert review["degraded"] is True


@pytest.mark.asyncio
async def test_governed_wrapper_preserves_payload(monkeypatch):
    monkeypatch.setattr("mindex_api.routers.worldview.avani_gateway.settings.avani_api_url", None)

    response = await wrap_governed_response(
        data=[{"id": "species-1"}],
        caller=_caller(),
        source_domains=["species"],
    )

    assert response["data"] == [{"id": "species-1"}]
    assert response["meta"]["avani"]["avani_verdict"] == "allow"
    assert response["meta"]["avani"]["degraded"] is True
