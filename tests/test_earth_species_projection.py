"""Real Earth handler/DTO, fake SQL transport; no database or provider calls.

SQL assertions bind stored-field and filter contracts, not PostgreSQL execution.
"""

import asyncio
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.elements import TextClause


ROOT = Path(__file__).resolve().parents[1]
OBSERVATION_ID = "2089944c-3510-4369-a05c-25f40e19b6f7"
TAXON_ID = "baa077cc-0f8e-4984-a593-dbe019bc0b4e"


@pytest.fixture
def route(monkeypatch):
    # Do not initialize app settings, engines or real dependencies at import.
    dependencies = ModuleType("mindex_api.dependencies")
    dependencies.get_db_session = lambda: None
    monkeypatch.setitem(sys.modules, dependencies.__name__, dependencies)
    name = "mindex_api.routers.earth_projection_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "mindex_api/routers/earth.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class Session:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    async def execute(self, statement, params):
        assert isinstance(statement, TextClause)
        self.calls.append((str(statement), dict(params)))
        return SimpleNamespace(fetchall=lambda: self.rows)


def query(route, session, **overrides):
    args = dict(layer="species", id=None, lat_min=-90, lat_max=90,
                lng_min=-180, lng_max=180, limit=2, offset=0,
                kingdom=None, session=session)
    args.update(overrides)
    return asyncio.run(route.map_bbox_query(**args))


def row(*, linked=True, properties=None):
    props = {
        "observation_id": OBSERVATION_ID,
        "taxon_id": TAXON_ID if linked else None,
        "canonical_taxon_uuid": TAXON_ID if linked else None,
        "canonical_name": "Agaricus fixture" if linked else None,
        "scientific_name": "Agaricus fixture" if linked else None,
        "common_name": "Fixture mushroom" if linked else None,
        "rank": "species" if linked else None,
        "kingdom": "Fungi" if linked else "Undesignated",
        "source_id": "observation-72", "external_id": "observation-72",
        "inat_id": "72", "taxon_inat_id": "91" if linked else None,
        "source_url": "https://example.invalid/observations/72",
        "observer": "fixture-observer", "notes": "Stored observation note",
        "accuracy_m": 0,
        "media": [
            {"url": "https://example.invalid/photo.jpg", "attribution": "Fixture author",
             "license_code": "cc-by", "type": "image"},
            {"url": "https://example.invalid/audio.ogg", "type": "audio", "license_code": None},
        ],
        "quality_grade": "research",
    }
    props.update(properties or {})
    return SimpleNamespace(id=OBSERVATION_ID, entity_type=props["kingdom"], domain="species",
                           name="Agaricus fixture (Fixture mushroom)" if linked else "Unidentified observation",
                           lat=0.0, lng=0.0, occurred_at="2026-10-02 12:00:00+00",
                           source="inat", properties=props)


def test_actual_handler_preserves_observation_identity_media_rights_and_accuracy(route):
    original = row()
    session = Session([original])
    response = query(route, session)
    actual = response.entities[0]
    assert actual.id == OBSERVATION_ID != TAXON_ID
    assert actual.lat == actual.lng == 0.0
    assert actual.properties == original.properties
    assert actual.properties["taxon_inat_id"] != actual.properties["inat_id"]
    assert actual.properties["media"][1]["type"] == "audio"
    assert actual.properties["accuracy_m"] == 0
    assert response.total == 1
    assert len(session.calls) == 1


def test_unlinked_and_image_less_observation_survives_dto(route):
    original = row(linked=False, properties={"media": [], "notes": None, "accuracy_m": None})
    response = query(route, Session([original]))
    actual = response.entities[0]
    assert actual.name == "Unidentified observation"
    assert actual.id == OBSERVATION_ID
    for field in ("taxon_id", "canonical_taxon_uuid", "canonical_name", "scientific_name", "rank"):
        assert actual.properties[field] is None
    assert actual.properties["kingdom"] == "Undesignated"
    assert actual.properties["media"] == []
    assert "image_url" not in actual.properties
    assert "genetics" not in actual.properties


def test_projection_uses_authoritative_stored_columns_and_distinct_provider_ids(route):
    sql = route._SPECIES_OBSERVATION_MAP_SQL
    base = (ROOT / "migrations/0001_init.sql").read_text(encoding="utf-8")
    for key in ("source_id", "observer", "notes", "accuracy_m", "media"):
        assert re.search(rf"\b{key}\s+(?:text|double precision|jsonb)", base)
        assert f"'{key}', o.{key}" in sql
    assert "'observation_id', o.id::text" in sql
    assert "'canonical_taxon_uuid', t.id::text" in sql
    assert "'inat_id', o.metadata->>'inat_id'" in sql
    provider = sql.split("'taxon_inat_id',", 1)[1].split(") as properties", 1)[0]
    assert "core.taxon_external_id" in provider and "e.taxon_id = t.id" in provider
    assert "e.source IN ('inat', 'inaturalist')" in provider
    assert "o.metadata->>'taxon_inat_id'" in provider
    assert "o.metadata->>'inat_id'" not in provider
    assert "LEFT JOIN core.taxon t ON t.id = o.taxon_id" in sql
    assert "'Unidentified observation'" in sql
    assert "'scientific_name', COALESCE(NULLIF(t.canonical_name, '')" in sql
    assert "'source_url', o.metadata->>'uri'" in sql
    assert "'rank', t.rank" in sql


def test_filter_is_validated_bound_before_outer_limit_not_post_filtered(route):
    session = Session([row()])
    response = query(route, session, kingdom="  fUnGi  ", limit=1)
    sql, params = session.calls[0]
    assert params["kingdom"] == "Fungi" and params["limit"] == 1
    assert sql == route._SPECIES_OBSERVATION_MAP_SQL
    assert sql.index("CAST(:kingdom AS text)") < sql.rindex("LIMIT :limit")
    assert "lower(COALESCE(t.kingdom, 'Undesignated'))" in sql
    assert "fUnGi" not in sql
    assert len(response.entities) == 1


def test_supported_kingdoms_match_authoritative_migration(route):
    ddl = (ROOT / "migrations/20260502_all_life_universal.sql").read_text(encoding="utf-8")
    check = ddl.split("kingdom IN (", 1)[1].split("));", 1)[0]
    assert set(re.findall(r"'([^']+)'", check)) == set(route._SPECIES_KINGDOMS)
    for kingdom in route._SPECIES_KINGDOMS:
        session = Session()
        query(route, session, kingdom=kingdom.lower())
        assert session.calls[0][1]["kingdom"] == kingdom


@pytest.mark.parametrize("invalid", ["", "fungal", "Fungi,Plantae", "Fungi' OR TRUE--"])
def test_invalid_filter_rejected_before_sql(route, invalid):
    session = Session()
    with pytest.raises(HTTPException) as caught:
        query(route, session, kingdom=invalid)
    assert caught.value.status_code == 422
    assert session.calls == []


def test_no_filter_and_sightings_alias_keep_all_life_contract(route):
    session = Session([row(), row(linked=False)])
    response = query(route, session, layer="sightings")
    sql, params = session.calls[0]
    assert params["kingdom"] is None
    assert sql == route._SPECIES_OBSERVATION_MAP_SQL
    assert "CAST(:kingdom AS text) IS NULL" in sql
    assert response.total == 2


def test_other_layer_query_and_parameters_are_not_changed_by_species_filter(route):
    session = Session()
    query(route, session, layer="aircraft", kingdom="not-a-species-filter")
    sql, params = session.calls[0]
    assert "FROM transport.aircraft" in sql
    assert "kingdom" not in sql and "kingdom" not in params


@pytest.mark.parametrize("layer", ["species", "sightings"])
@pytest.mark.parametrize("failed", [False, True])
def test_species_map_http_distinguishes_query_failure_from_empty(route, layer, failed, caplog):
    from fastapi import FastAPI
    import httpx

    class BoundarySession(Session):
        async def execute(self, statement, params):
            if failed:
                raise RuntimeError("sensitive-fixture-host: missing observation table")
            return await super().execute(statement, params)

    app = FastAPI()
    session = BoundarySession()

    async def db():
        yield session

    app.dependency_overrides[route.get_db_session] = db
    app.include_router(route.router)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/earth/map/bbox", params=dict(
                layer=layer, lat_min=37.43, lat_max=37.45,
                lng_min=-122.17, lng_max=-122.16, kingdom="Fungi", limit=25,
            ))

    response = asyncio.run(request())
    assert response.status_code == (503 if failed else 200)
    if failed:
        assert response.json() == {"detail": "Species map data unavailable"}
    else:
        assert response.json()["entities"] == []
        assert response.json()["total"] == 0
        assert response.json()["layer"] == layer
    assert "sensitive-fixture-host" not in response.text
    assert "sensitive-fixture-host" not in caplog.text
