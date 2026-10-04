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
from mindex_api.routers import taxon as route


DATABASE_URL = os.getenv("MINDEX_FILTERED_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DATABASE_URL, reason="private filtered-catalog PostgreSQL URL not configured"),
]


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
                id uuid PRIMARY KEY, taxon_id uuid NOT NULL REFERENCES core.taxon(id) ON DELETE CASCADE,
                source text NOT NULL, external_id text NOT NULL,
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
            """CREATE TABLE IF NOT EXISTS fungip.import_run (
                catalog_sha256 text PRIMARY KEY CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$'), manifest jsonb NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS fungip.species (
                species_id text PRIMARY KEY CHECK (species_id ~ '^FG[0-9]{3}$'),
                taxon_id uuid UNIQUE REFERENCES core.taxon(id) ON DELETE RESTRICT,
                accepted_name text NOT NULL, record jsonb NOT NULL, external_ids jsonb NOT NULL,
                verified_its_sequence text, image_valid boolean NOT NULL DEFAULT false,
                sequence_valid boolean NOT NULL DEFAULT false, missing_data_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
                validation_errors jsonb NOT NULL DEFAULT '[]'::jsonb,
                record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
                catalog_sha256 text NOT NULL REFERENCES fungip.import_run(catalog_sha256),
                resolution_status text NOT NULL CHECK (resolution_status IN
                    ('unresolved','resolved','identity_conflict','source_conflict','duplicate_canonical')))""",
            """CREATE TABLE IF NOT EXISTS fungip.page_verification (
                species_id text PRIMARY KEY REFERENCES fungip.species(species_id), taxon_id uuid NOT NULL REFERENCES core.taxon(id),
                record_sha256 text NOT NULL, canonical_url text NOT NULL, reviewer text NOT NULL,
                reviewed_at timestamptz NOT NULL, evidence jsonb NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS fungip.token_attempt (
                attempt_id uuid PRIMARY KEY, species_id text NOT NULL REFERENCES fungip.species(species_id),
                network text NOT NULL, status text NOT NULL)""",
        ):
            await connection.execute(text(ddl))

        # The database-name and loopback guards above make this reset private-fixture-only.
        for table in (
            "fungip.page_verification", "fungip.token_attempt", "fungip.species", "fungip.import_run",
            "core.taxon_external_id", "bio.taxon_trait", "bio.taxon_characteristic",
            "obs.observation", "media.image", "media.video", "media.audio", "bio.genome",
            "bio.taxon_compound", "bio.taxon_interaction", "bio.publication_taxon", "core.taxon",
        ):
            await connection.execute(text(f"DELETE FROM {table}"))

        now = datetime.now(timezone.utc)
        catalog_sha256 = "c" * 64
        await connection.execute(text(
            "INSERT INTO fungip.import_run(catalog_sha256, manifest) VALUES (:sha, '{}'::jsonb)"
        ), {"sha": catalog_sha256})
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
        family_conflict_id = uuid4()
        for taxon_id, name, metadata_family, source_id in (
            (linked_id, "Fixture Linked Agaric", None, "source-linked"),
            (conflict_id, "Fixture Conflicting Agaric", "Otheraceae", "source-conflict"),
            (ambiguous_id, "Fixture Ambiguous Agaric", "Otheraceae", "source-ambiguous"),
            (second_candidate_id, "Fixture Second Candidate", "Otheraceae", "source-second-candidate"),
            (family_conflict_id, "Fixture Family Conflict", "Primaryaceae", "source-family-conflict"),
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
            source_family = (
                "Otheraceae" if taxon_id == second_candidate_id else
                "Secondaryaceae" if taxon_id == family_conflict_id else "Agaricaceae"
            )
            source_image = (
                {"image_url": "https://fixture.invalid/species.jpg", "attribution": "FungiP credit",
                 "license_code": "CC-BY-4.0", "source_page": "https://fixture.invalid/source"}
                if taxon_id == linked_id else None
            )
            await connection.execute(text("""
                INSERT INTO fungip.species
                    (species_id, taxon_id, accepted_name, record, external_ids, resolution_status, image_valid,
                     sequence_valid, missing_data_flags, validation_errors, record_sha256, catalog_sha256)
                VALUES (:species_id, :taxon_id, :name, CAST(:record AS jsonb), CAST(:external_ids AS jsonb),
                        'resolved', :image_valid, false, '[]'::jsonb, '[]'::jsonb, :record_sha, :catalog_sha)
            """), {
                "species_id": {
                    linked_id: "FG026", conflict_id: "FG027", ambiguous_id: "FG028", second_candidate_id: "FG029",
                    family_conflict_id: "FG030",
                }[taxon_id], "taxon_id": taxon_id, "name": name,
                "record": json.dumps({
                    "accepted_name": name,
                    "taxonomy": {"family": source_family},
                    "image": source_image,
                }),
                "external_ids": json.dumps(fungip_external_ids),
                "image_valid": taxon_id == linked_id,
                "record_sha": "a" * 64, "catalog_sha": catalog_sha256,
            })
        for taxon_id, name, family, photo in (
            (uuid4(), "Fixture Photo Fallback", "Photoaceae", {
                "default_photo": {
                    "medium_url": "https://fixture.invalid/placeholder.svg", "attribution": "placeholder credit",
                },
                "photos": [{
                    "url": "https://fixture.invalid/fallback.jpg", "attribution": "fallback credit",
                    "license_code": "CC-BY-4.0",
                }],
            }),
            (uuid4(), "Fixture Unsafe Photo", "Unsafeaceae", {
                "default_photo": {"medium_url": "https://fixture.invalid\\unsafe.jpg"},
            }),
        ):
            await connection.execute(text("""
                INSERT INTO core.taxon
                    (id, canonical_name, rank, source, metadata, kingdom, lineage, lineage_ids,
                     external_ids, created_at, updated_at)
                VALUES (:id, :name, 'species', 'fixture', CAST(:metadata AS jsonb), 'Fungi',
                        ARRAY['Fungi']::text[], ARRAY[]::uuid[], '{}'::jsonb, :now, :now)
            """), {"id": taxon_id, "name": name,
                  "metadata": json.dumps({"family": family, **photo}), "now": now})
        # Add one genuinely source-qualified trait on the valid FungiP record for category matching.
        await connection.execute(text("""
            INSERT INTO bio.taxon_trait
                (id, taxon_id, trait_name, value_text, source, metadata, created_at, updated_at)
            VALUES (:id, :taxon_id, 'edibility', 'edible', 'fixture:source-a', '{}'::jsonb, :now, :now)
        """), {"id": uuid4(), "taxon_id": linked_id, "now": now})
        await connection.execute(text("""
            INSERT INTO bio.taxon_characteristic
                (id, taxon_id, name, value_text, source, metadata, created_at)
            VALUES (:id, :taxon_id, 'characteristic', 'gourmet', 'fixture:source-b', '{}'::jsonb, :now)
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
    assert response.data[-1].family == "Agaricaceae"
    assert any(e.source == "bio.taxon_trait" and e.value == "edible" for e in response.data[-1].category_evidence)
    assert any(e.source == "bio.taxon_characteristic" and e.value == "gourmet" for e in response.data[-1].category_evidence)


async def test_photo_family_join_requires_exact_source_identity(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        response = await _query(session, family="Agaricaceae", filter="has_images")
    assert [row.canonical_name for row in response.data] == ["Fixture Linked Agaric"]
    assert response.pagination.total == 1
    assert response.query.status == "available"
    assert response.data[0].image_selection.source == "fungip.species.record.image.image_url"
    assert response.data[0].image_selection.attribution == "FungiP credit"


async def test_unknown_family_does_not_match_taxa_with_a_valid_fungip_family(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        response = await _query(session, family="Unknown")
    assert response.data == []
    assert response.pagination.total == 0
    assert response.query.status == "empty"


async def test_conflicting_family_values_match_and_display_only_the_resolved_primary(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        primary = await _query(session, family="Primaryaceae")
        secondary = await _query(session, family="Secondaryaceae")
    assert [row.canonical_name for row in primary.data] == ["Fixture Family Conflict"]
    assert primary.pagination.total == 1
    assert primary.data[0].family == "Primaryaceae"
    assert [(item.source, item.value) for item in primary.data[0].family_evidence] == [
        ("core.taxon.metadata.family", "Primaryaceae"),
        ("fungip.species.record.taxonomy.family", "Secondaryaceae"),
    ]
    assert secondary.data == []
    assert secondary.pagination.total == 0


async def test_family_sort_uses_the_same_resolved_family_as_each_returned_row(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        response = await _query(
            session, order_by="family", pagination=PaginationParams(limit=137, offset=0),
        )
    families = [row.family for row in response.data]
    assert families == sorted(families)
    conflict = next(row for row in response.data if row.canonical_name == "Fixture Family Conflict")
    assert conflict.family == "Primaryaceae"


async def test_photo_fallback_uses_first_safe_stored_candidate_with_its_credit(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        response = await _query(session, family="Photoaceae", filter="has_images")
    assert response.pagination.total == 1
    assert response.data[0].image_selection.url == "https://fixture.invalid/fallback.jpg"
    assert response.data[0].image_selection.source == "core.taxon.metadata.photos[0].url"
    assert response.data[0].image_selection.attribution == "fallback credit"
    assert response.data[0].image_selection.license_code == "CC-BY-4.0"


async def test_unsafe_backslash_photo_does_not_count_as_usable(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        response = await _query(session, family="Unsafeaceae", filter="has_images")
    assert response.data == []
    assert response.pagination.total == 0


async def test_observation_sort_uses_stored_observation_relation(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        response = await _query(
            session, order_by="observations_count", order="desc",
            pagination=PaginationParams(limit=5, offset=0),
        )
    assert response.data[0].canonical_name == "Fixture Agaric 005"
    assert response.pagination.total == 137


async def test_list_total_reports_ttl_cache_after_new_matching_row_is_inserted(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        first = await _query(session, family="Agaricaceae", pagination=PaginationParams(limit=2, offset=0))
        assert first.pagination.total == 131
        now = datetime.now(timezone.utc)
        await session.execute(text("""
            INSERT INTO core.taxon
                (id, canonical_name, rank, source, metadata, kingdom, lineage, lineage_ids,
                 external_ids, created_at, updated_at)
            VALUES (:id, 'Fixture Newly Ingested Agaric', 'species', 'fixture',
                    '{"family":"Agaricaceae"}'::jsonb, 'Fungi', ARRAY['Fungi']::text[],
                    ARRAY[]::uuid[], '{}'::jsonb, :now, :now)
        """), {"id": uuid4(), "now": now})
        await session.commit()
        second = await _query(session, family="Agaricaceae", pagination=PaginationParams(limit=2, offset=0))
    assert second.pagination.total == 131
    assert len(second.data) == 2
    assert second.query.count_cache_state == "cache_hit"
    assert second.query.count_consistency == "best_effort_not_atomic"


async def test_family_sort_remains_native_when_optional_fungip_source_is_absent(db_sessionmaker):
    route._count_cache.clear()
    async with db_sessionmaker() as session:
        await session.execute(text("DROP TABLE fungip.species CASCADE"))
        await session.commit()
        response = await _query(
            session, order_by="family", pagination=PaginationParams(limit=1, offset=0),
        )
    assert response.data[0].canonical_name == "Fixture Agaric 000"
    assert response.query.status == "partial"
    assert response.query.filter_sources["family"] == "core.taxon.metadata.family"
