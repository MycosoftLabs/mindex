"""Offline all-species list/stats behavior; fake SQL results, no DB."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from mindex_api.contracts.v1.ancestry_index import FungiPIndexAvailability
from mindex_api.dependencies import PaginationParams
from mindex_api.routers import taxon as route


TAXON_ID = UUID("0c1f3a52-5d0e-4a51-9a7e-6f1f8d1c2b11")


class Result:
    def __init__(self, *, rows=None, scalar=None):
        self.rows, self.scalar = rows or [], scalar

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar

    def scalar_one_or_none(self):
        return self.scalar


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


def row():
    return {
        "id": TAXON_ID, "canonical_name": "Abies alba", "rank": "species", "common_name": "Silver fir",
        "author": "Mill.", "description": None, "source": "gbif", "metadata": {}, "kingdom": "Plantae",
        "lineage": ["Plantae", "Pinaceae"], "lineage_ids": [], "external_ids": {"gbif": "2685484"},
        "created_at": datetime(2026, 10, 3, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 3, tzinfo=timezone.utc),
        "obs_count": 0, "image_count": 0, "video_count": 0, "audio_count": 0, "genome_count": 0,
        "compound_link_count": 0, "interaction_count": 0, "publication_count": 0, "characteristic_count": 0,
    }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    route._count_cache.clear()

    async def members(db, taxon_ids):
        return {}, FungiPIndexAvailability(status="available")

    async def search_ids(db, query_pattern):
        return [], FungiPIndexAvailability(status="unavailable", reason="source_table_missing")

    monkeypatch.setattr(route, "load_public_fungip_members", members)
    monkeypatch.setattr(route, "search_validated_fungip_taxon_ids", search_ids)


async def call_list(db, **kwargs):
    defaults = dict(
        ids=None, q=None, rank=None, source=None, prefix=None, kingdom=None,
        lineage_contains=None, family=None, category=None, filter=None,
        order_by="canonical_name", order="asc",
    )
    defaults.update(kwargs)
    pagination = PaginationParams(limit=kwargs.pop("limit", 50), offset=kwargs.pop("offset", 0))
    defaults.pop("limit", None)
    defaults.pop("offset", None)
    return await route.list_taxa(pagination=pagination, db=db, **defaults)


@pytest.mark.asyncio
async def test_species_rank_includes_abbreviated_sources_and_uses_core_page_path():
    db = Session(Result(rows=[row()]), Result(scalar=3_500_000))
    response = await call_list(db, rank="species")
    page_sql, page_params = db.calls[0]
    assert "FROM core.taxon" in page_sql and "LIMIT :limit OFFSET :offset" in page_sql
    assert page_params["rank_variants"] == ["species", "sp."]
    assert response.pagination.total == 3_500_000
    assert response.data[0].canonical_name == "Abies alba"


@pytest.mark.asyncio
async def test_cached_total_reports_freshness_and_non_atomic_consistency():
    db = Session(Result(rows=[row()]), Result(scalar=42), Result(rows=[row()]))
    first = await call_list(db, rank="sp.", kingdom="Plantae")
    second = await call_list(db, rank="sp.", kingdom="Plantae", offset=50)
    assert len(db.calls) == 3
    assert first.pagination.total == 42
    assert second.pagination.total == 42
    assert first.query.count_cache_state == "fresh_query"
    assert second.query.count_cache_state == "cache_hit"
    assert second.query.count_consistency == "best_effort_not_atomic"
    assert second.query.count_cache_ttl_seconds == route._COUNT_CACHE_TTL_SECONDS


@pytest.mark.asyncio
async def test_cached_zero_does_not_label_a_nonempty_page_as_empty():
    db = Session(Result(rows=[]), Result(scalar=0), Result(rows=[row()]))
    await call_list(db, rank="species")
    response = await call_list(db, rank="species", offset=50)
    assert response.pagination.total == 0
    assert len(response.data) == 1
    assert response.query.count_cache_state == "cache_hit"
    assert response.query.status == "available"


@pytest.mark.asyncio
async def test_prefix_and_query_are_escaped_and_prefix_is_index_friendly():
    db = Session(Result(rows=[]), Result(scalar=0))
    await call_list(db, prefix="Ab_", q="50%")
    sql, params = db.calls[0]
    assert "lower(canonical_name) LIKE :prefix_pattern" in sql
    assert params["prefix_pattern"] == "ab\\_%"
    assert params["q_pattern"] == "%50\\%%"


@pytest.mark.asyncio
async def test_popular_sort_orders_by_stored_observation_count():
    db = Session(Result(scalar="fungip.species"), Result(rows=[row()]), Result(scalar=1))
    await call_list(db, order_by="observations_count", order="desc")
    assert "FROM obs.observation observation WHERE observation.taxon_id = t.id" in db.calls[1][0]
    assert "secondary_sort_key DESC" in db.calls[1][0]


@pytest.mark.asyncio
async def test_core_page_failure_falls_back_to_legacy_queries():
    db = Session(RuntimeError("boom"), Result(rows=[row()]), Result(scalar=7))
    response = await call_list(db, rank="species")
    assert db.rollbacks == 1
    assert "bio.taxon_full" in db.calls[1][0]
    assert response.pagination.total == 7


@pytest.mark.asyncio
async def test_stats_reports_real_grouped_counts_and_caches():
    db = Session(
        Result(rows=[{"kingdom": "Fungi", "species": 406_534}, {"kingdom": "Plantae", "species": 10}]),
        Result(rows=[{"source": "mycobank", "species": 406_523}]),
        Result(rows=[{"source": "gbif", "species": 30_604}]),
        Result(scalar=577_481),
    )
    payload = await route.taxa_stats(db=db)
    assert payload["species_total"] == 406_544
    assert payload["taxa_total"] == 577_481
    assert payload["by_linked_source"] == [{"source": "gbif", "species": 30_604}]
    assert await route.taxa_stats(db=db) is payload
    assert len(db.calls) == 4


@pytest.mark.asyncio
async def test_kingdom_filter_resolves_undesignated_imports_and_accepts_csv():
    db = Session(Result(rows=[row()]), Result(scalar=11))
    await call_list(db, rank="species,sp.", kingdom="Protozoa,Chromista")
    sql, params = db.calls[0]
    assert params["rank_variants"] == ["species", "sp."]
    assert params["kingdom_0"] == "Protozoa" and params["kingdom_1"] == "Chromista"
    assert "kingdom IN (:kingdom_0, :kingdom_1)" in sql
    assert "metadata->>'kingdom'" in sql and "iconic_taxon_name" in sql


@pytest.mark.asyncio
async def test_kingdom_all_is_unfiltered():
    db = Session(Result(rows=[]), Result(scalar=0))
    await call_list(db, kingdom="all")
    assert not any(key.startswith("kingdom_") for key in db.calls[0][1])


@pytest.mark.asyncio
async def test_kingdom_counts_group_by_effective_kingdom_and_cache():
    db = Session(Result(rows=[{"kingdom": "Fungi", "taxon_count": 441_280}, {"kingdom": "Animalia", "taxon_count": 6_858}]))
    payload = await route.taxa_kingdom_counts(rank="species", db=db)
    sql, params = db.calls[0]
    assert params["rank_variants"] == ["species", "sp."]
    assert "metadata->>'kingdom'" in sql and "GROUP BY 1" in sql
    assert payload["total"] == 448_138
    assert await route.taxa_kingdom_counts(rank="sp.", db=db) is not None
    assert await route.taxa_kingdom_counts(rank="species", db=db) is payload


@pytest.mark.asyncio
async def test_edible_filter_counts_matching_source_qualified_traits():
    db = Session(Result(rows=[row()]), Result(scalar=129))
    response = await call_list(db, category="edible", limit=120, offset=120)
    sql, params = db.calls[0]
    assert "FROM bio.taxon_trait trait WHERE trait.taxon_id = t.id" in sql
    assert "NULLIF(btrim(trait.source), '') IS NOT NULL" in sql
    assert params["category_0"] == "edible"
    assert params["category_1"] == "choice"
    assert params["category_2"] == "choice edible"
    assert response.pagination.total == 129
    assert response.query.status == "available"
    assert response.query.count_scope == "matching_core_taxa"


@pytest.mark.asyncio
async def test_unknown_category_excludes_only_known_explicit_values():
    db = Session(Result(rows=[]), Result(scalar=0))
    response = await call_list(db, category="unknown")
    sql, params = db.calls[0]
    assert "NOT (" in sql
    assert "metadata->'characteristics'" in sql
    assert "bio.taxon_characteristic characteristic" in sql
    assert params["known_category_0"] == "edible"
    assert response.query.status == "empty"


@pytest.mark.asyncio
async def test_description_filter_uses_persisted_fields_and_native_matching_total():
    db = Session(Result(rows=[row()]), Result(scalar=8))
    response = await call_list(db, filter="has_description")
    assert "NULLIF(btrim(t.description), '')" in db.calls[0][0]
    assert "t.metadata->>'description'" in db.calls[0][0]
    assert response.pagination.total == 8
    assert response.query.filter_sources["has_description"] == "core.taxon.description/metadata.description"


@pytest.mark.asyncio
async def test_missing_optional_fungip_photo_source_is_explicitly_partial():
    db = Session(Result(scalar=None), Result(rows=[row()]), Result(scalar=1))
    response = await call_list(db, filter="has_images")
    assert "default_photo" in db.calls[1][0]
    assert "fungip.species" not in db.calls[1][0]
    assert response.query.status == "partial"
    assert response.query.partial_reasons
    assert response.pagination.total == 1


@pytest.mark.asyncio
async def test_has_images_bounds_scan_with_indexed_candidate_superset():
    db = Session(Result(scalar="fungip.species"), Result(rows=[row()]), Result(scalar=1))
    await call_list(db, filter="has_images")
    page_sql, count_sql = db.calls[1][0], db.calls[2][0]
    candidates = (
        "t.id IN (SELECT c.id FROM core.taxon c WHERE c.metadata ?| array['default_photo', 'photos'] "
        "UNION SELECT source.taxon_id FROM fungip.species source "
        "WHERE source.image_valid IS TRUE AND source.taxon_id IS NOT NULL)"
    )
    for sql in (page_sql, count_sql):
        assert candidates in sql
        assert "t.metadata->'default_photo'->>'medium_url'" in sql
        assert "source.image_valid IS TRUE" in sql


@pytest.mark.asyncio
async def test_has_images_candidate_superset_omits_absent_fungip_source():
    db = Session(Result(scalar=None), Result(rows=[row()]), Result(scalar=1))
    await call_list(db, filter="has_images")
    assert "c.metadata ?| array['default_photo', 'photos']" in db.calls[1][0]
    assert "UNION" not in db.calls[1][0]


@pytest.mark.asyncio
async def test_family_sort_without_optional_source_still_uses_family_field():
    db = Session(Result(scalar=None), Result(rows=[row()]), Result(scalar=1))
    response = await call_list(db, order_by="family")
    sql = db.calls[1][0]
    assert "COALESCE(NULLIF(btrim(t.metadata->>'family'), ''), 'Unknown') AS sort_key" in sql
    assert response.query.status == "partial"


@pytest.mark.asyncio
async def test_family_filter_and_featured_sort_use_native_page_sql():
    db = Session(Result(scalar="fungip.species"), Result(rows=[row()]), Result(scalar=1))
    response = await call_list(db, family="Agaricaceae", order_by="featured", limit=120)
    sql, params = db.calls[1]
    assert "source.taxon_id = t.id" in sql
    assert "matches.candidate_count = 1" in sql
    assert params["family"] == "Agaricaceae"
    assert "5000 THEN 0 ELSE 1 END" in sql
    assert response.query.status == "available"


@pytest.mark.asyncio
async def test_family_filter_uses_one_resolved_value_for_match_and_count():
    db = Session(Result(scalar="fungip.species"), Result(rows=[]), Result(scalar=0))
    await call_list(db, family="Unknown")
    sql, params = db.calls[1]
    assert "COALESCE(NULLIF(btrim(t.metadata->>'family'), ''), fungip_family.family, 'Unknown') = :family" in sql
    assert "ORDER BY source.species_id ASC" in sql
    assert params["family"] == "Unknown"
    count_sql, _ = db.calls[2]
    assert "LEFT JOIN LATERAL" in count_sql and "fungip_family.family" in count_sql


def test_family_projection_keeps_primary_and_source_disagreement():
    item = row()
    item["metadata"] = {"family": "Coreaceae"}
    member = SimpleNamespace(taxonomy={"family": "FungiPaceae"}, species_id="FG026")
    route._project_family(item, member)
    assert item["family"] == "Coreaceae"
    assert item["family_source"] == "core.taxon.metadata.family"
    assert [e["value"] for e in item["family_evidence"]] == ["Coreaceae", "FungiPaceae"]
    assert item["family_evidence"][1]["species_id"] == "FG026"


def test_category_evidence_projects_persisted_source_values_with_a_hard_bound():
    projected, truncated = route._project_category_evidence([
        {"source": "bio.taxon_trait", "value": "Choice-Edible"},
        {"source": "bio.taxon_characteristic", "value": "medicinal"},
        {"source": "unknown", "value": "poisonous"},
    ])
    assert {item["category"] for item in projected} == {"edible", "gourmet", "medicinal"}
    assert all(item["source"] != "unknown" for item in projected)
    assert truncated is False
    bounded, truncated = route._project_category_evidence([
        {"source": "bio.taxon_trait", "value": "edible"} for _ in range(65)
    ])
    assert len(bounded) == 1
    assert truncated is True


def test_image_selection_skips_unsafe_placeholder_and_keeps_selected_credit_atomic():
    item = row()
    item["metadata"] = {
        "default_photo": {"medium_url": "https://example.test/placeholder.svg", "attribution": "wrong credit"},
        "photos": [{"url": "https://example.test/valid.jpg", "attribution": "valid credit", "license_code": "CC-BY"}],
    }
    member = SimpleNamespace(image=None)
    selected = route._project_image_selection(item, member)
    assert selected == {
        "url": "https://example.test/valid.jpg",
        "source": "core.taxon.metadata.photos[0].url",
        "attribution": "valid credit",
        "license_code": "CC-BY",
        "source_url": "https://example.test/valid.jpg",
    }
    item["metadata"]["default_photo"] = {"medium_url": "https://example.test\\unsafe.jpg"}
    assert route._project_image_selection(item, member)["url"] == "https://example.test/valid.jpg"


@pytest.mark.asyncio
async def test_fungip_enrichment_error_fails_closed_instead_of_returning_an_unexplained_page():
    from fastapi import HTTPException

    async def failed_members(db, taxon_ids):
        return {}, FungiPIndexAvailability(status="error", reason="query_failed")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(route, "load_public_fungip_members", failed_members)
    try:
        db = Session(Result(scalar="fungip.species"), Result(rows=[row()]), Result(scalar=1))
        with pytest.raises(HTTPException) as exc:
            await call_list(db, family="FungiPaceae")
        assert exc.value.status_code == 503
        assert "FungiP enrichment" in exc.value.detail
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_fungip_enrichment_error_does_not_overturn_a_genuine_zero_count():
    async def failed_members(db, taxon_ids):
        return {}, FungiPIndexAvailability(status="error", reason="query_failed")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(route, "load_public_fungip_members", failed_members)
    try:
        db = Session(Result(scalar="fungip.species"), Result(rows=[]), Result(scalar=0))
        response = await call_list(db, family="NoSuchFamily")
        assert response.pagination.total == 0
        assert response.query.status == "empty"
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_unsupported_category_is_not_reported_as_empty():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await call_list(Session(), category="derived-from-name")
    assert exc.value.status_code == 422
