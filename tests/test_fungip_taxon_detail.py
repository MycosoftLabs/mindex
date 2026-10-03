"""Offline UUID-detail composition; fake SQL results, no DB or background execution."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, HTTPException

from mindex_api.contracts.v1.ancestry_index import FungiPIndexAvailability, FungiPIndexMember
from mindex_api.routers import taxon as route


TAXON_ID = UUID("b745422d-daf8-452b-b175-2a30d1bf7530")


class Result:
    def __init__(self, *, row=None, scalar=None, rows=None):
        self.row, self.scalar, self.rows = row, scalar, rows or []

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def scalar_one_or_none(self):
        return self.scalar

    def all(self):
        return self.rows


class Session:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def rollback(self):
        self.rollbacks += 1


def ordinary(*, rank="species", traits=None):
    return {
        "id": TAXON_ID, "canonical_name": "Agaricus fixture", "rank": rank,
        "common_name": "Fixture", "author": "Fixture author", "description": "Fixture description",
        "source": "fixture", "metadata": {"preserved": {"value": 7}}, "kingdom": "Fungi",
        "lineage": ["Fungi", "Agaricaceae"], "lineage_ids": [], "external_ids": {"fixture": "42"},
        "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 2, tzinfo=timezone.utc), "traits": traits,
    }


def assert_ordinary_preserved(response, original):
    for key, value in original.items():
        assert getattr(response, key) == (value or [] if key == "traits" else value)


@pytest.mark.asyncio
async def test_uuid_detail_attaches_exact_member_without_changing_ordinary_data(monkeypatch):
    row = ordinary(traits=[{"id": 1, "trait_name": "habitat", "value_text": "wood"}])
    before = deepcopy(row)
    member = FungiPIndexMember(
        species_id="FG026", mindex_uuid=TAXON_ID, canonical_taxon_uuid=TAXON_ID,
        accepted_name=row["canonical_name"], reference_dna_available=True,
        feature_label="Research collection member", resolution_status="resolved",
        identity_state="linked", identity_reason="resolved_unique_exact_external_id",
        record_sha256="a" * 64, catalog_sha256="b" * 64, chain_receipt_status="not_confirmed",
    )
    session = Session(Result(row=row))
    helper_calls = []

    async def members(db, taxon_ids):
        helper_calls.append((db, taxon_ids))
        return {str(TAXON_ID): member}, FungiPIndexAvailability(status="available")

    monkeypatch.setattr(route, "load_public_fungip_members", members)
    background = BackgroundTasks()
    response = await route.get_taxon(TAXON_ID, background, db=session, _api_key=None)
    assert helper_calls == [(session, [TAXON_ID])]
    assert response.fungip == member
    assert response.fungip_index.status == "available"
    assert response.id == TAXON_ID
    assert response.canonical_name == row["canonical_name"]
    assert response.metadata == row["metadata"]
    assert response.external_ids == row["external_ids"]
    assert response.traits[0].trait_name == "habitat"
    assert response.traits[0].value_text == "wood"
    encoded = response.model_dump(mode="json")
    assert encoded["fungip"]["mindex_uuid"] == str(TAXON_ID)
    assert encoded["fungip_index"] == {"status": "available", "reason": None}
    assert row == before
    assert len(background.tasks) == 1  # Existing enrichment scheduling, deliberately not executed.
    assert background.tasks[0].func is route._queue_incomplete_taxon
    assert len(session.calls) == 1
    assert session.calls[0][1] == {"taxon_id": str(TAXON_ID)}


@pytest.mark.asyncio
async def test_unmapped_uuid_keeps_ordinary_detail_and_available_annotation_state():
    row = ordinary()
    before = deepcopy(row)
    session = Session(Result(row=row), Result(scalar="fungip.species"), Result(rows=[]))
    response = await route.get_taxon(TAXON_ID, BackgroundTasks(), db=session, _api_key=None)
    assert_ordinary_preserved(response, row)
    assert response.fungip is None
    assert response.fungip_index.status == "available"
    assert session.calls[-1][1] == {"taxon_ids": [TAXON_ID]}
    assert session.rollbacks == 0
    assert not session.results
    assert row == before


@pytest.mark.asyncio
async def test_missing_collection_table_keeps_nonfungal_ordinary_detail_and_unavailable_state():
    row = ordinary(rank="genus")
    row.update(canonical_name="Plant fixture", kingdom="Plantae", lineage=["Plantae"])
    before = deepcopy(row)
    session = Session(Result(row=row), Result(scalar=None))
    background = BackgroundTasks()
    response = await route.get_taxon(TAXON_ID, background, db=session, _api_key=None)
    assert_ordinary_preserved(response, row)
    assert response.fungip is None
    assert response.model_dump(mode="json")["fungip_index"] == {
        "status": "unavailable", "reason": "source_table_missing",
    }
    assert len(session.calls) == 2
    assert session.rollbacks == 0
    assert not background.tasks
    assert row == before


@pytest.mark.asyncio
async def test_optional_query_error_preserves_detail_and_reports_error_without_exception_prose():
    row = ordinary()
    session = Session(Result(row=row), Result(scalar="fungip.species"), RuntimeError("fixture query failed"))
    response = await route.get_taxon(TAXON_ID, BackgroundTasks(), db=session, _api_key=None)
    assert_ordinary_preserved(response, row)
    assert response.fungip is None
    assert response.fungip_index == FungiPIndexAvailability(status="error", reason="query_failed")
    assert "fixture query failed" not in response.model_dump_json()
    assert session.rollbacks == 1
    assert not session.results


@pytest.mark.asyncio
async def test_missing_taxon_remains_404_without_optional_query_or_background_task(monkeypatch):
    async def forbidden(*args):
        raise AssertionError("Optional helper must not run for a missing taxon")

    monkeypatch.setattr(route, "load_public_fungip_members", forbidden)
    session = Session(Result(row=None))
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as failure:
        await route.get_taxon(TAXON_ID, background, db=session, _api_key=None)
    assert failure.value.status_code == 404
    assert failure.value.detail == "Taxon not found"
    assert len(session.calls) == 1
    assert not background.tasks
