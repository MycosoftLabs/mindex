"""Explorer list-to-detail contracts using real routes and offline SQL fixtures."""

from copy import deepcopy

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from mindex_api.contracts.v1.ancestry_index import FungiPIndexAvailability, FungiPIndexMember
from mindex_api.routers import taxon as route
from test_fungip_taxon_detail import TAXON_ID, Result, Session, ordinary
from test_taxon_all_species_list import Result as ListResult


@pytest.fixture
def catalog(monkeypatch):
    row = ordinary()
    row["metadata"].update(
        family="Core family",
        default_photo={
            "url": "https://example.org/core.jpg",
            "attribution": "Core photographer",
            "license_code": "CC-BY-4.0",
        },
    )
    member = FungiPIndexMember(
        species_id="FG026", mindex_uuid=TAXON_ID, canonical_taxon_uuid=TAXON_ID,
        accepted_name=row["canonical_name"], reference_dna_available=False,
        feature_label="Research collection member", resolution_status="resolved",
        identity_state="linked", identity_reason="resolved_unique_exact_external_id",
        record_sha256="a" * 64, catalog_sha256="b" * 64,
        chain_receipt_status="not_confirmed", taxonomy={"family": "Source family"},
        image={
            "image_url": "https://example.org/source.jpg",
            "attribution": "Source photographer", "license_code": "CC0",
        },
    )
    route._count_cache.clear()
    yield row, member
    route._count_cache.clear()


@pytest.mark.parametrize("source_status", ["available", "unavailable", "error"])
def test_search_to_read_only_detail_preserves_identity_and_projection(
    monkeypatch, catalog, source_status
):
    row, member = catalog
    before = deepcopy(row)
    availability = FungiPIndexAvailability(
        status=source_status,
        reason={"available": None, "unavailable": "source_table_missing", "error": "query_failed"}[source_status],
    )

    async def members(db, ids):
        assert ids == [TAXON_ID]
        return ({str(TAXON_ID): member} if source_status == "available" else {}), availability

    async def search_ids(db, query):
        return [], availability

    def no_enrichment(*args, **kwargs):
        raise AssertionError("Browsing scheduled enrichment")

    monkeypatch.setattr(route, "load_public_fungip_members", members)
    monkeypatch.setattr(route, "search_validated_fungip_taxon_ids", search_ids)
    monkeypatch.setattr(route, "_queue_incomplete_taxon", no_enrichment)
    listing = Session(ListResult(rows=[deepcopy(row)]), ListResult(scalar=1))
    detail = Session(Result(row=deepcopy(row)))
    sessions = iter([listing, detail])
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[route.require_api_key] = lambda: None
    app.dependency_overrides[route.get_db_session] = lambda: next(sessions)
    with TestClient(app) as client:
        response = client.get("/taxa", params={"q": "Agaricus", "limit": 1, "offset": 0})
        assert response.status_code == 200
        page = response.json()
        selected = page["data"][0]
        response = client.get(f"/taxa/{selected['id']}", params={"read_only": "true"})
        assert response.status_code == 200
        shown = response.json()

    for key in ("id", "canonical_name", "source", "external_ids", "family",
                "family_source", "family_evidence", "image_selection"):
        assert shown[key] == selected[key], key
    assert shown["id"] == str(TAXON_ID)
    assert shown["family"] == "Core family"
    image = shown["image_selection"]
    if source_status == "available":
        assert len(shown["family_evidence"]) == 2
        assert image["url"] == "https://example.org/source.jpg"
        assert image["attribution"] == "Source photographer" and image["license_code"] == "CC0"
    else:
        assert len(shown["family_evidence"]) == 1
        assert image["url"] == "https://example.org/core.jpg"
        assert image["attribution"] == "Core photographer" and image["license_code"] == "CC-BY-4.0"
    assert shown["fungip_index"] == availability.model_dump()
    assert page["query"]["status"] == ("available" if source_status == "available" else "partial")
    assert page["pagination"] == {"limit": 1, "offset": 0, "total": 1}
    assert detail.calls[0][1] == {"taxon_id": str(TAXON_ID)}
    assert not listing.results and not detail.results
    assert row == before


@pytest.mark.asyncio
async def test_detail_with_no_family_or_usable_image_reports_unknown(monkeypatch):
    row = ordinary()
    row["metadata"]["default_photo"] = {"url": "javascript:invalid", "attribution": "Unused"}

    async def members(db, ids):
        return {}, FungiPIndexAvailability(status="available")

    monkeypatch.setattr(route, "load_public_fungip_members", members)
    background = BackgroundTasks()
    result = await route.get_taxon(
        TAXON_ID, background, db=Session(Result(row=row)), _api_key=None, read_only=True
    )
    assert result.family == "Unknown" and result.family_source == "unknown"
    assert result.family_evidence == [] and result.image_selection is None
    assert not background.tasks
