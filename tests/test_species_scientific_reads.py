"""Actual scientific route handlers with bounded fake SQL; no live database calls."""
import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from mindex_api.dependencies import PaginationParams
from mindex_api.routers import genetics, observations

TAXON = UUID("8948f374-9439-4165-b592-33e3bf9a8998")
OBS = UUID("2ba4d126-71ad-4853-96c3-4a60b8c15c68")


class Result:
    def __init__(self, scalar=None, rows=()):
        self.scalar = scalar
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.scalar

    def scalar_one(self):
        return self.scalar

    def mappings(self):
        return SimpleNamespace(all=lambda: self.rows)


class Session:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((str(sql), params or {}))
        return self.results.pop(0)


def genetic_query(session, **updates):
    args = dict(pagination=PaginationParams(25, 0), db=session, search=None, gene=None,
                source=None, species=None, taxon_id=None, kingdom=None, min_length=None,
                max_length=None)
    args.update(updates)
    return asyncio.run(genetics.list_genetic_sequences(**args))


def sequence():
    return dict(id=7, accession="FIXTURE", version="FIXTURE.1", taxon_id=TAXON,
                species_name="Fixture species", gene="ITS", region=None,
                sequence="ACGTN", sequence_length=5, sequence_type="dna", source="fixture",
                source_url=None, definition=None, organism=None, pubmed_id=None, doi=None, metadata={})


def test_genetics_filters_exact_uuid_in_count_and_page_and_returns_identity():
    session = Session(Result("bio.genetic_sequence"), Result(1), Result(rows=[sequence()]))
    result = genetic_query(session, taxon_id=TAXON)
    assert result.data[0].taxon_id == TAXON
    assert result.data[0].accession_version == "FIXTURE.1"
    assert result.data[0].sequence == "ACGTN"
    assert result.pagination == dict(limit=25, offset=0, total=1)
    for sql, params in session.calls[1:]:
        assert "gs.taxon_id = :taxon_id" in sql
        assert params["taxon_id"] == str(TAXON)
        assert "ILIKE" not in sql
    assert "gs.taxon_id," in session.calls[-1][0]


def test_missing_genetic_store_is_unavailable_not_empty():
    session = Session(Result(None))
    with pytest.raises(HTTPException) as error:
        genetic_query(session, taxon_id=TAXON)
    assert error.value.status_code == 503
    assert len(session.calls) == 1


def test_available_genetic_store_can_return_genuine_empty_page():
    session = Session(Result("bio.genetic_sequence"), Result(0), Result())
    result = genetic_query(session, taxon_id=TAXON)
    assert result.data == [] and result.pagination["total"] == 0


def test_existing_name_search_stays_available_and_cannot_override_uuid_filter():
    session = Session(Result("bio.genetic_sequence"), Result(0), Result())
    genetic_query(session, taxon_id=TAXON, species="Fixture")
    for sql, params in session.calls[1:]:
        assert "gs.species_name ILIKE :species" in sql
        assert "AND gs.taxon_id = :taxon_id" in sql
        assert params["species"] == "%Fixture%"


@pytest.mark.parametrize("read_only", [True, False])
def test_observation_read_only_only_suppresses_events(monkeypatch, read_only):
    events = []
    monkeypatch.setattr(observations, "schedule_domain_event", lambda **kw: events.append(kw))
    stored = dict(id=OBS, taxon_id=TAXON, source="fixture", source_id="42", observer=None,
                  observed_at="2026-10-02T00:00:00Z", accuracy_m=0, media=[], notes=None,
                  metadata={}, latitude=0, longitude=0)
    session = Session(Result(rows=[stored]))
    result = asyncio.run(observations.list_observations(
        pagination=PaginationParams(25, 0), db=session, taxon_id=TAXON, kingdom=None,
        start=None, end=None, bbox=None, include_total=False, read_only=read_only))
    assert result.data[0].taxon_id == TAXON
    assert result.data[0].location.coordinates == [0, 0]
    assert result.pagination.total is None
    assert len(events) == (0 if read_only else 1)
    assert len(session.calls) == 1
    assert "o.taxon_id = :taxon_id" in session.calls[0][0]
    assert "ORDER BY o.observed_at DESC, o.id" in session.calls[0][0]


def test_fastapi_exposes_exact_uuid_and_read_only_contracts():
    route = next(r for r in genetics.router.routes if r.endpoint is genetics.list_genetic_sequences)
    field = next(p for p in route.dependant.query_params if p.name == "taxon_id")
    parsed, error = field.validate(str(TAXON), {}, loc=("query", "taxon_id"))
    assert error is None and parsed == TAXON
    _, error = field.validate("FG015", {}, loc=("query", "taxon_id"))
    assert error is not None
    route = next(r for r in observations.router.routes if r.endpoint is observations.list_observations)
    flag = next(p for p in route.dependant.query_params if p.name == "read_only")
    assert flag.default is False
    parsed, error = flag.validate("true", {}, loc=("query", "read_only"))
    assert error is None and parsed is True
