"""Offline biological query contracts, bound to the repository's real DDL.

The session checks referenced table/column names and returns typed row fixtures.
It does not execute SQL or claim PostgreSQL/extension/data qualification.
"""
import asyncio
from contextlib import nullcontext
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import UUID

import pytest

from test_unified_search_session_contract import route  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
TAXON_ID = UUID("14f8d4bd-6f6b-4720-8ebf-40b2533e75a7")
COMPOUND_ID = UUID("00000000-0000-4000-8000-000000000007")
DDL = {
    "core.taxon": "0001_init.sql",
    "bio.taxon_trait": "0001_init.sql",
    "bio.compound": "0007_compounds.sql",
    "bio.taxon_compound": "0007_compounds.sql",
    "bio.genetic_sequence": "0012_genetics.sql",
    "obs.observation": "0001_init.sql",
    "species.organisms": "20260315_earth_scale_domains.sql",
    "species.sightings": "20260315_earth_scale_domains.sql",
}


def declared_columns(table):
    source = (ROOT / "migrations" / DDL[table]).read_text(encoding="utf-8")
    body = re.search(
        rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\((.*?)\n\);",
        source, re.S | re.I,
    ).group(1)
    return {
        match.group(1).lower()
        for line in body.splitlines()
        if (match := re.match(r"\s*(\w+)\s+(?:uuid|text|varchar|serial|integer|bigint|double|jsonb|geography|timestamptz|timestamp|date)\b", line, re.I))
    }


class DdlSession:
    """A bounded name checker, deliberately not a general SQL interpreter."""

    def __init__(self, rows=(), unavailable=None):
        self.rows = rows if isinstance(rows, dict) else list(rows)
        self.unavailable = unavailable
        self.queries = []
        self.rollbacks = 0

    def begin_nested(self):
        return nullcontext()

    async def execute(self, statement, params):
        sql = str(statement)
        self.queries.append((sql, params))
        aliases = {}
        for match in re.finditer(r"\b(?:FROM|JOIN)\s+([a-z_]+\.[a-z_]+)(?:\s+([a-z_]+))?", sql, re.I):
            table, alias = match.groups()
            if table not in DDL or table == self.unavailable:
                raise RuntimeError(f"relation {table} does not exist")
            if alias and alias.lower() not in {"where", "order", "limit", "left", "join"}:
                aliases[alias] = declared_columns(table)
        for alias, column in re.findall(r"\b([a-z_]+)\.([a-z_]+)\b", sql, re.I):
            if alias in aliases and column not in aliases[alias]:
                raise RuntimeError(f"column {alias}.{column} does not exist")
        rows = self.rows
        if isinstance(rows, dict):
            rows = next((values for table, values in rows.items() if f"FROM {table} " in sql), [])
        return SimpleNamespace(fetchall=lambda: rows)

    async def rollback(self):
        self.rollbacks += 1


def taxon_row(**changes):
    fields = dict(id=TAXON_ID, canonical_name="Ganoderma sichuanense", common_name=None,
                  rank="species", description=None, image_url=None, observation_count=0,
                  toxicity=None, edibility=None)
    return SimpleNamespace(**(fields | changes))


def compound_row(**changes):
    fields = dict(id=COMPOUND_ID, name="Schema fixture compound", formula=None,
                  molecular_weight=None, chemical_class=None, smiles=None, species=[])
    return SimpleNamespace(**(fields | changes))


def genetic_row(**changes):
    fields = dict(id=7, accession="SCHEMA_FIXTURE_7", species_name=None, gene=None,
                  sequence_length=4, source="unite")
    return SimpleNamespace(**(fields | changes))


@pytest.mark.parametrize("domain,row", [
    ("taxa", taxon_row()), ("compounds", compound_row()), ("genetics", genetic_row()),
])
def test_actual_queries_reference_declared_tables_columns_and_keep_bound_values(route, domain, row):
    session = DdlSession([row])
    query = "' OR TRUE --"
    result = asyncio.run(getattr(route, "search_" + domain)(session, query, 3))
    assert len(result) == 1
    sql, params = session.queries[0]
    assert query not in sql
    assert params == {"query": "%" + query + "%", "exact_query": query, "limit": 3}
    assert session.rollbacks == 0


def test_taxon_uuid_unknown_traits_and_absent_media_are_preserved(route):
    result = asyncio.run(route.search_taxa(DdlSession([taxon_row()]), "Ganoderma", 1))[0]
    assert result["id"] == result["mindex_uuid"] == str(TAXON_ID)
    assert result["edibility"] is result["toxicity"] is result["image_url"] is None


def test_edibility_filter_uses_declared_trait_text_and_metadata_without_a_taxon_column(route):
    session = DdlSession([taxon_row(edibility="edible")])
    result = asyncio.run(route.search_taxa(session, "Ganoderma", 1, toxicity_filter="edible"))
    sql, _ = session.queries[0]
    assert "bio.taxon_trait" in sql and "trait_name = 'edibility'" in sql
    assert "value_text" in sql and "t.metadata->>'edibility'" in sql
    assert "t.edibility" not in sql
    assert result[0]["edibility"] == "edible"


def test_trait_fallback_sql_requires_nonblank_agreement_not_latest_assertion(route):
    session = DdlSession([taxon_row(edibility=None)])
    result = asyncio.run(route.search_taxa(session, "Ganoderma", 1))
    sql = " ".join(session.queries[0][0].split())
    assert "CASE WHEN COUNT(DISTINCT NULLIF(BTRIM(tt.value_text), '')) = 1" in sql
    assert "THEN MIN(NULLIF(BTRIM(tt.value_text), '')) ELSE NULL END AS value_text" in sql
    assert "COALESCE(t.metadata->>'edibility', trait_edibility.value_text)" in sql
    assert "tt.updated_at" not in sql
    # A null consensus (including conflicting stored assertions) is not promoted.
    assert result[0]["edibility"] is None


def test_compound_uuid_and_only_stored_taxon_associations_are_preserved(route):
    session = DdlSession([compound_row(species=["Ganoderma sichuanense"])])
    result = asyncio.run(route.search_compounds(session, "Ganoderma", 2))[0]
    sql, _ = session.queries[0]
    assert "bio.taxon_compound" in sql and "core.taxon" in sql
    assert "compound_id = c.id" in sql and "taxon_id" in sql
    assert "producing_species" not in sql
    assert result["id"] == str(COMPOUND_ID)
    assert result["source_species"] == ["Ganoderma sichuanense"]
    assert result["formula"] is None and result["bioactivity"] == []


def test_genetics_keeps_nullable_species_distinct_from_gene_region_and_source(route):
    session = DdlSession([genetic_row()])
    result = asyncio.run(route.search_genetics(session, "ITS2", 1))[0]
    sql, _ = session.queries[0]
    assert "region ILIKE :query" in sql
    assert result["id"] == 7 and result["species_name"] is None
    assert result["gene"] is None and result["source"] == "unite"
    assert result["sequence_length"] == 4


@pytest.mark.parametrize("domain", ["taxa", "compounds", "genetics"])
def test_successful_empty_declared_table_is_empty_without_rollback(route, domain):
    session = DdlSession()
    assert asyncio.run(getattr(route, "search_" + domain)(session, "absent", 2)) == []
    assert session.rollbacks == 0


def test_missing_real_genetics_table_remains_an_error_and_recovers_session(route):
    session = DdlSession(unavailable="bio.genetic_sequence")
    with pytest.raises(route._DomainQueryError, match="genetics query failed"):
        asyncio.run(route.search_genetics(session, "ITS", 1))
    assert session.rollbacks == 1
    assert asyncio.run(route.search_taxa(session, "Ganoderma", 1)) == []
    assert session.rollbacks == 1


@pytest.mark.parametrize("radius,expected_m", [(None, 100000), (0, 0), (2.5, 2500)])
def test_taxa_proximity_uses_bound_actual_observation_location(route, radius, expected_m):
    session = DdlSession()
    asyncio.run(route.search_taxa(session, "Ganoderma", 3, lat=0, lng=0, radius=radius))
    sql, params = session.queries[0]
    assert "FROM obs.observation nearby" in sql and "nearby.taxon_id = t.id" in sql
    assert "ST_DWithin(nearby.location" in sql
    assert "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography" in sql
    assert params["lat"] == params["lng"] == 0 and params["radius_m"] == expected_m


@pytest.mark.parametrize("coordinates", [{}, {"lat": 0, "lng": 0, "radius": 0}])
def test_observations_use_real_schema_with_and_without_location_filter(route, coordinates):
    observation_id = "00000000-0000-4000-8000-000000000008"
    row = SimpleNamespace(id=observation_id, taxon_id=str(TAXON_ID),
                          taxon_name="Ganoderma sichuanense", location=None,
                          lat=0.0, lng=0.0, observed_at="2026-10-03T00:00:00Z",
                          image_url=None, source="inat")
    session = DdlSession({"obs.observation": [row], "species.sightings": []})
    result = asyncio.run(route.search_observations(session, "Ganoderma", 2, **coordinates))
    assert len(session.queries) == 2 and len(result) == 1
    assert result[0]["id"] == observation_id and result[0]["taxon_id"] == str(TAXON_ID)
    assert result[0]["source"] == "inat" and result[0]["location"] is None
    assert result[0]["lat"] == result[0]["lng"] == 0 and result[0]["image_url"] is None
    sql, params = session.queries[0]
    assert "ST_Y(o.location::geometry)" in sql and "core.observation" not in sql
    assert "location_name" not in sql and "o.geom" not in sql
    for query_sql, query_params in session.queries:
        assert ("ST_DWithin" in query_sql) == bool(coordinates)
        if coordinates:
            assert query_params["radius_m"] == 0


def test_unlinked_observation_keeps_unknown_identity_and_location(route):
    row = SimpleNamespace(id="00000000-0000-4000-8000-000000000009", taxon_id=None,
                          taxon_name=None, location=None, lat=None, lng=None,
                          observed_at="2026-10-03T00:00:00Z", image_url=None, source="gbif")
    session = DdlSession({"obs.observation": [row], "species.sightings": []})
    result = asyncio.run(route.search_observations(session, "", 2))
    assert result[0]["taxon_id"] is result[0]["taxon_name"] is result[0]["lat"] is None
    assert "LEFT JOIN core.taxon" in session.queries[0][0]
    assert session.queries[0][1]["query"] == "%%"
