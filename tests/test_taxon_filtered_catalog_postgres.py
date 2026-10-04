"""Real SQL checks for the native filter contract; requires a private fixture database URL."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mindex_api.dependencies import PaginationParams
from mindex_api.contracts.v1.ancestry_index import FungiPIndexAvailability
from mindex_api.routers import taxon as route


DATABASE_URL = os.getenv("MINDEX_FILTERED_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DATABASE_URL, reason="private filtered-catalog PostgreSQL URL not configured"),
]


@pytest.fixture(autouse=True)
def no_page_enrichment(monkeypatch):
    async def members(db, taxon_ids):
        return {}, FungiPIndexAvailability(status="available")

    monkeypatch.setattr(route, "load_public_fungip_members", members)


@pytest_asyncio.fixture
async def db_sessionmaker():
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as connection:
        guard = (await connection.execute(text(
            "SELECT current_database() AS database_name, host(inet_server_addr()) AS server_address"
        ))).mappings().one()
        assert guard["database_name"].startswith("mindex_filtered_fixture_"), "refusing non-fixture database"
        assert guard["server_address"] in {"127.0.0.1", "::1"}, "refusing non-loopback PostgreSQL"
        for ddl in (
            "CREATE SCHEMA IF NOT EXISTS core", "CREATE SCHEMA IF NOT EXISTS bio",
            "CREATE SCHEMA IF NOT EXISTS obs", "CREATE SCHEMA IF NOT EXISTS media",
            "CREATE SCHEMA IF NOT EXISTS fungip",
            """CREATE TABLE IF NOT EXISTS core.taxon (
                id uuid PRIMARY KEY, canonical_name text NOT NULL, rank text NOT NULL,
                common_name text, author text, authority text, description text, source text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb, kingdom text, lineage text[], lineage_ids uuid[],
                external_ids jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL,
                updated_at timestamptz NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS core.taxon_external_id (
                id uuid PRIMARY KEY, taxon_id uuid NOT NULL, source text NOT NULL, external_id text NOT NULL,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL,
                UNIQUE (source, external_id))""",
            """CREATE TABLE IF NOT EXISTS bio.taxon_trait (
                id uuid PRIMARY KEY, taxon_id uuid NOT NULL, trait_name text NOT NULL, value_text text,
                value_numeric double precision, value_unit text, source text,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS bio.taxon_characteristic (
                id uuid PRIMARY KEY, taxon_id uuid NOT NULL, name text NOT NULL, value_text text,
                value_num double precision, units text, source text, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL)""",
            "CREATE TABLE IF NOT EXISTS obs.observation (id uuid PRIMARY KEY, taxon_id uuid NOT NULL)",
            "CREATE TABLE IF NOT EXISTS media.image (id uuid PRIMARY KEY, taxon_id uuid)",
            "CREATE TABLE IF NOT EXISTS media.video (id uuid PRIMARY KEY, taxon_id uuid)",
            "CREATE TABLE IF NOT EXISTS media.audio (id uuid PRIMARY KEY, taxon_id uuid)",
            "CREATE TABLE IF NOT EXISTS bio.genome (id uuid PRIMARY KEY, taxon_id uuid NOT NULL)",
            "CREATE TABLE IF NOT EXISTS bio.taxon_compound (id uuid PRIMARY KEY, taxon_id uuid NOT NULL)",
            "CREATE TABLE IF NOT EXISTS bio.taxon_interaction (id uuid PRIMARY KEY, source_taxon_id uuid, target_taxon_id uuid)",
            "CREATE TABLE IF NOT EXISTS bio.publication_taxon (id uuid PRIMARY KEY, taxon_id uuid NOT NULL)",
            """CREATE TABLE IF NOT EXISTS fungip.species (
                species_id text PRIMARY KEY, taxon_id uuid UNIQUE, accepted_name text NOT NULL, record jsonb NOT NULL,
                external_ids jsonb NOT NULL, resolution_status text NOT NULL, image_valid boolean NOT NULL,
                validation_errors jsonb NOT NULL DEFAULT '[]'::jsonb)""",
        ):
            await connection.execute(text(ddl))

        # The database-name and loopback guards above make this reset private-fixture-only.
        for table in (
            "fungip.species", "core.taxon_external_id", "bio.taxon_trait", "bio.taxon_characteristic",
            "obs.observation", "media.image", "media.video", "media.audio", "bio.genome",
            "bio.taxon_compound", "bio.taxon_interaction", "bio.publication_taxon", "core.taxon",
        ):
            await connection.execute(text(f"DELETE FROM {table}"))

        now = datetime.now(timezone.utc)
        matching_ids: list[UUID] = []
        for index in range(130):
            taxon_id = uuid4()
            matching_ids.append(taxon_id)
            await connection.execute(text("""
                INSERT INTO core.taxon
                    (id, canonical_name, rank, common_name, description, source, metadata, kingdom,
                     lineage, lineage_ids, external_ids, created_at, updated_at)
                VALUES (:id, :name, 'species', NULL, :description, 'fixture',
                        CAST(:metadata AS jsonb), 'Fungi', ARRAY['Fungi','Agaricales']::text[],
                        ARRAY[]::uuid[], '{}'::jsonb, :now, :now)
            """), {
                "id": taxon_id, "name": f"Fixture Agaric {index:03d}",
                "description": "synthetic stored description" if index % 2 == 0 else None,
                "metadata": json.dumps({"family": "Agaricaceae"}), "now": now,
            })
            await connection.execute(text("""
                INSERT INTO bio.taxon_trait
                    (id, taxon_id, trait_name, value_text, source, metadata, created_at, updated_at)
                VALUES (:id, :taxon_id, 'edibility', 'choice_edible', 'fixture:source-a', '{}'::jsonb, :now, :now)
            """), {"id": uuid4(), "taxon_id": taxon_id, "now": now})
        # One exact source-linked family/image row is included; the conflicting identifier row is excluded.
        linked_id, conflict_id, ambiguous_id, second_candidate_id = uuid4(), uuid4(), uuid4(), uuid4()
        for taxon_id, name, metadata_family, source_id in (
            (linked_id, "Fixture Linked Agaric", None, "source-linked"),
            (conflict_id, "Fixture Conflicting Agaric", "Otheraceae", "source-conflict"),
            (ambiguous_id, "Fixture Ambiguous Agaric", "Otheraceae", "source-ambiguous"),
            (second_candidate_id, "Fixture Second Candidate", "Otheraceae", "source-second-candidate"),
        ):
            await connection.execute(text("""
                INSERT INTO core.taxon
                    (id, canonical_name, rank, source, metadata, kingdom, lineage, lineage_ids,
                     external_ids, created_at, updated_at)
                VALUES (:id, :name, 'species', 'fixture', CAST(:metadata AS jsonb), 'Fungi',
                        ARRAY['Fungi']::text[], ARRAY[]::uuid[], '{}'::jsonb, :now, :now)
            """), {
                "id": taxon_id, "name": name,
                "metadata": json.dumps({"family": metadata_family}) if metadata_family else "{}",
                "now": now,
            })
            source_ids = [("fixture_source", source_id)]
            if taxon_id == conflict_id:
                source_ids = [("fixture_source", "identifier-does-not-match")]
            elif taxon_id == ambiguous_id:
                source_ids = [("fixture_source_a", "ambiguous-a")]
            elif taxon_id == second_candidate_id:
                source_ids = [("fixture_source_b", "ambiguous-b")]
            for source_name, external_id in source_ids:
                await connection.execute(text("""
                    INSERT INTO core.taxon_external_id (id, taxon_id, source, external_id, metadata, created_at)
                    VALUES (:id, :taxon_id, :source, :external_id, '{}'::jsonb, :now)
                """), {
                    "id": uuid4(), "taxon_id": taxon_id, "source": source_name,
                    "external_id": external_id, "now": now,
                })
            fungip_external_ids = [{"source": "fixture_source", "external_id": source_id}]
            if taxon_id == ambiguous_id:
                fungip_external_ids = [
                    {"source": "fixture_source_a", "external_id": "ambiguous-a"},
                    {"source": "fixture_source_b", "external_id": "ambiguous-b"},
                ]
            await connection.execute(text("""
                INSERT INTO fungip.species
                    (species_id, taxon_id, accepted_name, record, external_ids, resolution_status, image_valid)
                VALUES (:species_id, :taxon_id, :name, CAST(:record AS jsonb), CAST(:external_ids AS jsonb),
                        'resolved', :image_valid)
            """), {
                "species_id": str(taxon_id), "taxon_id": taxon_id, "name": name,
                "record": json.dumps({
                    "accepted_name": name,
                    "taxonomy": {
                        "family": "Otheraceae" if taxon_id == second_candidate_id else "Agaricaceae"
                    },
                    "image": {"image_url": "https://fixture.invalid/species.jpg"} if taxon_id != second_candidate_id else None,
                }),
                "external_ids": json.dumps(fungip_external_ids),
                "image_valid": taxon_id != second_candidate_id,
            })
        # Add one genuinely source-qualified trait on the valid FungiP record for category matching.
        await connection.execute(text("""
            INSERT INTO bio.taxon_trait
                (id, taxon_id, trait_name, value_text, source, metadata, created_at, updated_at)
            VALUES (:id, :taxon_id, 'edibility', 'edible', 'fixture:source-a', '{}'::jsonb, :now, :now)
        """), {"id": uuid4(), "taxon_id": linked_id, "now": now})
        await connection.execute(text("""
            INSERT INTO obs.observation (id, taxon_id) VALUES (:id, :taxon_id)
        """), {"id": uuid4(), "taxon_id": matching_ids[5]})
        await connection.execute(text("""
            INSERT INTO obs.observation (id, taxon_id) VALUES (:id, :taxon_id)
        """), {"id": uuid4(), "taxon_id": matching_ids[5]})
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _query(session, **kwargs):
    defaults = dict(
        pagination=PaginationParams(limit=120, offset=0), db=session,
        ids=None, q=None, rank="species", source=None, prefix=None, kingdom=None,
        lineage_contains=None, family=None, category=None, filter=None,
        order_by="canonical_name", order="asc",
    )
    defaults.update(kwargs)
    return await route.list_taxa(**defaults)


async def test_native_filtered_count_and_page_extend_beyond_first_120(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        response = await _query(
            session, family="Agaricaceae", category="edible",
            pagination=PaginationParams(limit=120, offset=120),
        )
    assert len(response.data) == 11
    assert response.pagination.total == 131
    assert response.data[-1].canonical_name == "Fixture Linked Agaric"
    assert response.query.status == "available"


async def test_photo_family_join_requires_exact_source_identity(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        response = await _query(session, family="Agaricaceae", filter="has_images")
    assert [row.canonical_name for row in response.data] == ["Fixture Linked Agaric"]
    assert response.pagination.total == 1
    assert response.query.status == "available"


async def test_observation_sort_uses_stored_observation_relation(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        response = await _query(
            session, order_by="observations_count", order="desc",
            pagination=PaginationParams(limit=5, offset=0),
        )
    assert response.data[0].canonical_name == "Fixture Agaric 005"
    assert response.pagination.total == 134


async def test_family_sort_remains_native_when_optional_fungip_source_is_absent(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        await session.execute(text("DROP TABLE fungip.species"))
        await session.commit()
        response = await _query(
            session, order_by="family", pagination=PaginationParams(limit=1, offset=0),
        )
    assert response.data[0].canonical_name == "Fixture Agaric 000"
    assert response.query.status == "partial"
    assert response.query.filter_sources["family"] == "core.taxon.metadata.family"
