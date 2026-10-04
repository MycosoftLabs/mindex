from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

from mindex_api.routers.compounds import get_compounds_for_taxon


class Result:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class Session:
    def __init__(self):
        self.calls = []
        self.results = [
            Result(row=SimpleNamespace(id=UUID("6db28640-67fb-4808-90de-956a856366f7"),
                                       canonical_name="Schizophyllum commune", common_name="splitgill")),
            Result(rows=[SimpleNamespace(
                compound_id=UUID("9a92672b-3f4f-4b3a-a093-4d4c2ea6be32"),
                name="Example metabolite",
                formula="C2H4O2",
                molecular_weight=60.05,
                chemspider_id=123,
                pubchem_id=456,
                relationship_type="contains",
                evidence_level="reported",
                tissue_location="fruiting body",
                compound_source="pubchem",
                association_source="study",
                source_url="https://doi.org/10.1234/example",
                doi="10.1234/example",
            )]),
        ]
        self.rollbacks = 0

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def rollback(self):
        self.rollbacks += 1


def test_exact_taxon_compounds_preserve_pubchem_and_association_citation():
    # Synthetic rows verify projection shape; they assert no live compound link.
    session = Session()
    taxon_id = UUID("6db28640-67fb-4808-90de-956a856366f7")

    result = asyncio.run(get_compounds_for_taxon(taxon_id=taxon_id, session=session))

    assert result.taxon_id == taxon_id
    assert result.data_state == "available"
    assert result.schema_state == "ready"
    assert result.scope == "exact_taxon"
    assert len(result.compounds) == 1
    compound = result.compounds[0]
    assert compound.pubchem_id == 456
    assert compound.compound_source == "pubchem"
    assert compound.association_source == "study"
    assert compound.source_url == "https://doi.org/10.1234/example"
    assert compound.doi == "10.1234/example"
    query, params = session.calls[1]
    assert "tc.taxon_id = :taxon_id" in query
    assert "tc.source_url" in query and "tc.doi" in query
    assert params["taxon_id"] == taxon_id


def test_exact_compound_projection_schema_failure_returns_503_not_empty():
    session = Session()
    session.results = [session.results[0], RuntimeError("missing provenance column")]

    import asyncio
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        asyncio.run(get_compounds_for_taxon(
            taxon_id=UUID("6db28640-67fb-4808-90de-956a856366f7"), session=session,
        ))

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "compound_schema_or_query_unavailable"
    assert "missing provenance column" not in str(raised.value.detail)
    assert session.rollbacks == 1
