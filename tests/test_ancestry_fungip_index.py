"""Offline contracts for the read-only FungiP source index."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import HTTPException

from mindex_api.routers.taxon import _FUNGIP_INDEX_CTE, _fungip_taxon_index_row, list_fungip_taxon_index
from mindex_api.services.ancestry_public_members import project_fungip_identity


CANONICAL_UUID = UUID("6eb9e962-05c3-4f2b-8ed1-598cbb2a0ac2")


class _Result:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def mappings(self):
        return self

    def one(self):
        return self._one

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.rollback_calls = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.results.pop(0)

    async def rollback(self):
        self.rollback_calls += 1


def _base_row(**overrides):
    row = {
        "species_id": "FG026",
        "stored_taxon_id": None,
        "accepted_name": "Agaricus exampleus",
        "record": {
            "accepted_name": "Agaricus exampleus",
            "requested_name": "Agaricus exampleus",
            "common_name": "Example mushroom",
            "group": "agaric",
            "ticker": "FGX",
            "synonyms": ["Historical source name"],
            "catalog_review_flags": ["review_open"],
            "dna": {"accession_version": "OP123456.1"},
            "taxonomy": {"KINGDOM": "Fungi"},
            "image": {"url": "https://example.invalid/image.jpg"},
        },
        "external_ids": [{"source": "gbif", "external_id": "12345"}],
        "verified_its_sequence": None,
        "image_valid": True,
        "sequence_valid": True,
        "missing_data_flags": ["canonical_uuid_unresolved"],
        "validation_errors": [],
        "record_sha256": "a" * 64,
        "catalog_sha256": "b" * 64,
        "resolution_status": "unresolved",
        "candidate_count": 0,
        "candidate_taxon_id": None,
        "candidate_taxon_ids": [],
        "canonical_name": None,
        "canonical_rank": None,
        "canonical_kingdom": "Fungi",
        "canonical_common_name": None,
        "canonical_metadata": None,
        "page_taxon_id": None,
        "page_canonical_url": None,
        "page_record_sha256": None,
        "page_evidence": None,
        "obs_count": None,
        "token_confirmed": False,
    }
    row.update(overrides)
    return row


def test_identity_projection_requires_stored_resolved_exact_crosswalk_species_and_name():
    unresolved_with_candidate = _base_row(
        candidate_count=1, candidate_taxon_id=CANONICAL_UUID,
        candidate_taxon_ids=[CANONICAL_UUID], canonical_rank="species",
        canonical_kingdom="Fungi",
        canonical_name="Agaricus exampleus",
    )
    assert project_fungip_identity(unresolved_with_candidate) == (
        "unresolved", "unresolved", None,
    )
    unresolved_fk_conflict = _base_row(
        stored_taxon_id=UUID("7c4543cc-f8ee-4be9-8c44-88a1e80dcd78"),
        candidate_count=1, candidate_taxon_id=CANONICAL_UUID,
        candidate_taxon_ids=[CANONICAL_UUID], canonical_rank="species",
        canonical_kingdom="Fungi", canonical_name="Agaricus exampleus",
    )
    assert project_fungip_identity(unresolved_fk_conflict) == (
        "unresolved", "unresolved", None,
    )

    linked = _base_row(
        resolution_status="resolved", stored_taxon_id=CANONICAL_UUID,
        candidate_count=1, candidate_taxon_id=CANONICAL_UUID,
        candidate_taxon_ids=[CANONICAL_UUID], canonical_rank="species",
        canonical_kingdom="Fungi",
        canonical_name="Agaricus exampleus",
    )
    assert project_fungip_identity(linked) == (
        "linked", "unique_exact_external_id", str(CANONICAL_UUID),
    )
    assert project_fungip_identity({**linked, "canonical_rank": "Species"}) == (
        "linked", "unique_exact_external_id", str(CANONICAL_UUID),
    )

    assert project_fungip_identity({**linked, "stored_taxon_id": None})[0] == "ambiguous"
    assert project_fungip_identity({**linked, "candidate_taxon_id": UUID(int=2)})[0] == "ambiguous"
    assert project_fungip_identity({**linked, "candidate_count": 2})[0] == "ambiguous"
    assert project_fungip_identity({**linked, "canonical_rank": "genus"})[0] == "ambiguous"
    assert project_fungip_identity({**linked, "canonical_kingdom": "Animalia"}) == (
        "ambiguous", "canonical_kingdom_mismatch", None,
    )
    assert project_fungip_identity({**linked, "canonical_name": "Other name"})[0] == "ambiguous"
    assert project_fungip_identity({**linked, "record": {"accepted_name": "Changed name"}})[0] == "ambiguous"
    assert project_fungip_identity({**linked, "record": {}})[0] == "ambiguous"
    assert project_fungip_identity({**linked, "resolution_status": "source_conflict"})[0] == "ambiguous"

    assert "s.resolution_status = 'resolved' AND COALESCE(matches.candidate_count, 0) = 1" in _FUNGIP_INDEX_CTE
    assert "LOWER(t.rank) = 'species'" in _FUNGIP_INDEX_CTE
    assert "LOWER(t.rank) IS DISTINCT FROM 'species'" in _FUNGIP_INDEX_CTE
    assert "t.canonical_name IS DISTINCT FROM s.record->>'accepted_name'" in _FUNGIP_INDEX_CTE
    assert "s.accepted_name IS DISTINCT FROM s.record->>'accepted_name'" in _FUNGIP_INDEX_CTE


def test_taxon_index_keeps_stable_source_identity_and_withholds_stale_page():
    row = _base_row(
        resolution_status="resolved", stored_taxon_id=CANONICAL_UUID,
        candidate_count=1, candidate_taxon_id=CANONICAL_UUID,
        candidate_taxon_ids=[CANONICAL_UUID], canonical_rank="species",
        canonical_kingdom="Fungi",
        canonical_name="Agaricus exampleus", canonical_common_name="Example mushroom",
        canonical_metadata={"source": "catalog"}, obs_count=11,
        page_taxon_id=CANONICAL_UUID,
        page_canonical_url=f"https://mycosoft.com/natureos/ancestry/species/{CANONICAL_UUID}",
        page_record_sha256="a" * 64,
        page_evidence={key: True for key in (
            "name_checked", "taxonomy_checked", "dna_checked", "download_checked", "attribution_checked",
        )},
    )
    projected = _fungip_taxon_index_row(row)
    assert projected.id == CANONICAL_UUID
    assert projected.obs_count == 11
    assert projected.fungip.species_id == "FG026"
    assert projected.fungip.mindex_uuid == CANONICAL_UUID
    assert projected.fungip.canonical_url.endswith(str(CANONICAL_UUID))

    launch_association = {
        "species_id": "FG026",
        **{key: "a" * 64 for key in (
            "snapshot_sha256", "payload_sha256", "catalog_sha256", "launch_sha256",
            "handoff_sha256", "correction_sha256", "dna_sha256", "image_sha256",
        )},
        "launch_schema": "v1", "superseded_encoding": "utf-8",
        "corrected_input_version": "v1", "authority_approval_reference": "approval-1",
        "source_reported_as_of_utc": "2026-10-02T00:00:00Z",
        "source_reported_as_of_pt": "2026-10-01T17:00:00-07:00",
        "correction_recorded_at_utc": "2026-10-02T00:00:00Z",
        "ticker": "FGX", "accepted_name": "Agaricus exampleus",
        "dna_accession_version": "OP123456.1", "dna_database": "GenBank",
        "dna_source_url": "https://example.invalid/dna", "image_credit": "Example",
        "image_license": "CC-BY", "launch_status": "candidate", "source_hash_match": True,
        "canonical_approved": False, "owner_entity": "Mycosoft", "verification_basis": "fixture",
        "synonyms": ["Historical source name", "Launch synonym"],
        "catalog_review_flags": ["review_open", "launch_review"],
    }
    merged = _fungip_taxon_index_row(row, launch_association)
    assert merged.fungip.synonyms == ["Historical source name", "Launch synonym"]
    assert merged.fungip.catalog_review_flags == ["review_open", "launch_review"]

    stale_page = _fungip_taxon_index_row({**row, "page_record_sha256": "c" * 64})
    assert stale_page.fungip.canonical_url is None


@pytest.mark.asyncio
async def test_collection_returns_unresolved_rows_and_accepts_explorer_page_size():
    session = _FakeSession([
        _Result(one={"species_table": "fungip.species", "launch_table": None, "batch_table": None}),
        _Result(one={"total": 1, "linked": 0, "unresolved": 1, "ambiguous": 0}),
        _Result(rows=[_base_row()]),
    ])
    response = await list_fungip_taxon_index(
        q="FG026", offset=0, limit=500, kingdom="Fungi", rank="species",
        prefix="Aga", order_by="observations_count", order="desc", db=session,
    )

    assert response.pagination.total == 1
    assert response.pagination.limit == 500
    assert response.counts.linked == 0
    assert response.counts.unresolved == 1
    assert response.launch_index.state == "unavailable"
    assert response.data[0].id is None
    assert response.data[0].canonical_name is None
    assert response.data[0].fungip.species_id == "FG026"
    assert response.data[0].fungip.identity_state == "unresolved"
    assert response.data[0].fungip.requested_name == "Agaricus exampleus"
    assert response.data[0].fungip.synonyms == ["Historical source name"]
    assert response.data[0].fungip.catalog_review_flags == ["review_open"]
    assert session.calls[1][1]["query_pattern"] == "%FG026%"
    assert session.calls[1][1]["prefix_pattern"] == "Aga%"
    assert "indexed.record->>'requested_name' ILIKE :query_pattern" in session.calls[1][0]
    assert "observations_count" not in session.calls[1][0]
    assert "obs_count DESC" in session.calls[2][0]
    assert session.rollback_calls == 0


@pytest.mark.asyncio
async def test_missing_collection_table_is_an_explicit_service_unavailable():
    session = _FakeSession([
        _Result(one={"species_table": None, "launch_table": None, "batch_table": None}),
    ])
    with pytest.raises(HTTPException) as error:
        await list_fungip_taxon_index(
            q=None, offset=0, limit=300, kingdom=None, rank=None, prefix=None,
            order_by="canonical_name", order="asc", db=session,
        )

    assert error.value.status_code == 503
    assert "no fallback source" in error.value.detail
    assert len(session.calls) == 1
    assert session.rollback_calls == 0
