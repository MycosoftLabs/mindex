"""Read-only canonical detail: actual handler/helper with fake SQL, no queue execution."""
from copy import deepcopy

import pytest
from fastapi import BackgroundTasks, HTTPException

from mindex_api.contracts.v1.ancestry_index import FungiPIndexAvailability, FungiPIndexMember
from mindex_api.routers import taxon as route
from test_fungip_taxon_detail import TAXON_ID, Result, Session, ordinary, assert_ordinary_preserved


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [True, False])
@pytest.mark.parametrize("rank", ["species", "genus"])
async def test_flag_only_changes_queue_not_ordinary_or_optional_detail(read_only, rank):
    row = ordinary(rank=rank)
    before = deepcopy(row)
    session = Session(Result(row=row), Result(scalar=None))
    background = BackgroundTasks()
    response = await route.get_taxon(TAXON_ID, background, db=session, _api_key=None, read_only=read_only)
    assert_ordinary_preserved(response, row)
    assert response.fungip is None
    assert response.fungip_index == FungiPIndexAvailability(status="unavailable", reason="source_table_missing")
    assert len(background.tasks) == int(not read_only and rank == "species")
    if background.tasks:
        assert background.tasks[0].func is route._queue_incomplete_taxon
        assert background.tasks[0].args[0] == str(TAXON_ID)
    assert len(session.calls) == 2 and session.rollbacks == 0
    assert row == before


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [True, False])
async def test_mapped_fungip_composition_is_identical_in_both_modes(monkeypatch, read_only):
    row = ordinary()
    member = FungiPIndexMember(
        species_id="FG026", mindex_uuid=TAXON_ID, canonical_taxon_uuid=TAXON_ID,
        accepted_name=row["canonical_name"], reference_dna_available=True,
        feature_label="Research collection member", resolution_status="resolved",
        identity_state="linked", identity_reason="resolved_unique_exact_external_id",
        record_sha256="a" * 64, catalog_sha256="b" * 64, chain_receipt_status="not_confirmed",
    )
    calls = []

    async def members(db, ids):
        calls.append((db, ids))
        return {str(TAXON_ID): member}, FungiPIndexAvailability(status="available")

    monkeypatch.setattr(route, "load_public_fungip_members", members)
    session = Session(Result(row=row))
    background = BackgroundTasks()
    response = await route.get_taxon(TAXON_ID, background, db=session, _api_key=None, read_only=read_only)
    assert_ordinary_preserved(response, row)
    assert response.fungip == member and response.fungip_index.status == "available"
    assert calls == [(session, [TAXON_ID])]
    assert len(background.tasks) == int(not read_only)


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [True, False])
async def test_optional_query_error_preserves_response_and_recovery(read_only):
    row = ordinary()
    session = Session(Result(row=row), Result(scalar="fungip.species"), RuntimeError("fixture private error"))
    background = BackgroundTasks()
    response = await route.get_taxon(TAXON_ID, background, db=session, _api_key=None, read_only=read_only)
    assert_ordinary_preserved(response, row)
    assert response.fungip is None
    assert response.fungip_index == FungiPIndexAvailability(status="error", reason="query_failed")
    assert "fixture private error" not in response.model_dump_json()
    assert session.rollbacks == 1 and not session.results
    assert len(background.tasks) == int(not read_only)


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [True, False])
async def test_missing_row_stays_404_with_no_optional_query_or_queue(monkeypatch, read_only):
    async def forbidden(*args):
        raise AssertionError("optional helper was reached for missing row")

    monkeypatch.setattr(route, "load_public_fungip_members", forbidden)
    session = Session(Result(row=None))
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as exc:
        await route.get_taxon(TAXON_ID, background, db=session, _api_key=None, read_only=read_only)
    assert exc.value.status_code == 404 and exc.value.detail == "Taxon not found"
    assert len(session.calls) == 1 and not background.tasks


@pytest.mark.asyncio
async def test_omitted_flag_preserves_existing_species_queue():
    session = Session(Result(row=ordinary()), Result(scalar=None))
    background = BackgroundTasks()
    await route.get_taxon(TAXON_ID, background, db=session, _api_key=None)
    assert len(background.tasks) == 1
    assert background.tasks[0].func is route._queue_incomplete_taxon


def test_fastapi_exposes_optional_boolean_query_with_false_default():
    endpoint = next(item for item in route.router.routes if item.endpoint is route.get_taxon)
    query = next(item for item in endpoint.dependant.query_params if item.name == "read_only")
    assert not query.required and query.default is False
    for value, expected in [("true", True), ("false", False)]:
        parsed, error = query.validate(value, {}, loc=("query", "read_only"))
        assert error is None and parsed is expected
