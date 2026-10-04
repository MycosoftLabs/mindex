from __future__ import annotations

from uuid import UUID

import pytest

from mindex_api.routers.phylogeny import get_phylogeny


ANIMALIA_ID = UUID("e8c03e91-444e-48ed-9d7d-6d44c5486c0b")
FUNGI_ID = UUID("6db28640-67fb-4808-90de-956a856366f7")
ANCESTOR_ID = UUID("f3aa8e1f-56c8-4f90-b0aa-0c2b9dd24cb1")


class MappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.statements = []

    async def execute(self, statement, params):
        self.statements.append((str(statement), params))
        assert self.responses, "Unexpected SQL query"
        return self.responses.pop(0)


def flatten(root):
    result = []
    while root:
        result.append(root)
        root = root["children"][0] if root["children"] else None
    return result


@pytest.mark.asyncio
async def test_real_animalia_capture_keeps_selected_uuid_off_animalia_root():
    """Regression for the retained Bucephala albeola browser/API reproduction."""
    selected = {
        "id": ANIMALIA_ID,
        "kingdom": "Animalia",
        "canonical_name": "Bucephala albeola",
        "rank": "species",
        "lineage": ["Animalia", "Chordata", "Aves", "Anseriformes", "Anatidae", "Bucephala"],
        # Reproduces the unsafe inclusive/self UUID being positionally attached
        # to the root name by the previous projector.
        "lineage_ids": [ANIMALIA_ID, None, None, None, None, None],
    }
    db = Session(MappingsResult([selected]), MappingsResult([selected]))

    result = await get_phylogeny(taxon_id=ANIMALIA_ID, db=db)
    nodes = flatten(result["tree"])

    assert nodes[0]["name"] == "Animalia"
    assert nodes[0]["taxon_id"] is None
    assert nodes[0]["id"] == "name:Animalia"
    assert nodes[-2]["name"] == "Bucephala"
    assert nodes[-2]["rank"] == "unknown"
    assert nodes[-1]["name"] == "Bucephala albeola"
    assert nodes[-1]["taxon_id"] == str(ANIMALIA_ID)
    assert nodes[-1]["rank"] == "species"
    assert result["status"] == "partial"
    assert result["lineage_provenance"]["raw_lineage_ids"] == [str(ANIMALIA_ID), None, None, None, None, None]
    assert any(issue["reason"] == "lineage_uuid_identity_mismatch" for issue in result["lineage_provenance"]["issues"])


@pytest.mark.asyncio
async def test_real_fungi_selected_identity_is_tip_and_missing_ancestors_are_partial():
    """Use the retained Fungi search identity; unknown ancestor IDs stay name-only."""
    selected = {
        "id": FUNGI_ID,
        "kingdom": "Fungi",
        "canonical_name": "Schizophyllum commune",
        "rank": "species",
        "lineage": ["Fungi", "Basidiomycota", "Agaricomycetes", "Agaricales", "Schizophyllaceae", "Schizophyllum"],
        "lineage_ids": [None, None, None, None, None, None],
    }
    result = await get_phylogeny(taxon_id=FUNGI_ID, db=Session(MappingsResult([selected])))
    nodes = flatten(result["tree"])

    assert [node["name"] for node in nodes] == [
        "Fungi", "Basidiomycota", "Agaricomycetes", "Agaricales",
        "Schizophyllaceae", "Schizophyllum", "Schizophyllum commune",
    ]
    assert all(node["taxon_id"] is None for node in nodes[:-1])
    assert all(node["rank"] == "unknown" for node in nodes[:-1])
    assert nodes[-1]["taxon_id"] == str(FUNGI_ID)
    assert nodes[-1]["rank"] == "species"
    assert result["lineage_provenance"]["status"] == "partial"


@pytest.mark.asyncio
async def test_verified_ancestor_uses_its_own_name_and_rank_and_misalignment_drops_links():
    aligned = {
        "id": FUNGI_ID,
        "kingdom": "Fungi",
        "canonical_name": "Schizophyllum commune",
        "rank": "species",
        "lineage": ["Fungi", "Schizophyllaceae"],
        "lineage_ids": [ANCESTOR_ID, None],
    }
    ancestor = {
        "id": ANCESTOR_ID,
        "kingdom": "Fungi",
        "canonical_name": "Fungi",
        "rank": "kingdom",
    }
    verified = await get_phylogeny(
        taxon_id=FUNGI_ID,
        db=Session(MappingsResult([aligned]), MappingsResult([ancestor])),
    )
    assert verified["tree"]["taxon_id"] == str(ANCESTOR_ID)
    assert verified["tree"]["rank"] == "kingdom"
    assert verified["tree"]["children"][0]["taxon_id"] is None

    misaligned = {**aligned, "lineage_ids": [ANCESTOR_ID]}
    partial = await get_phylogeny(taxon_id=FUNGI_ID, db=Session(MappingsResult([misaligned])))
    nodes = flatten(partial["tree"])
    assert nodes[0]["taxon_id"] is None
    assert nodes[0]["rank"] == "unknown"
    assert partial["lineage_provenance"]["alignment"] == "misaligned"
    assert partial["lineage_provenance"]["raw_lineage_ids"] == [str(ANCESTOR_ID)]
    assert partial["status"] == "partial"


@pytest.mark.asyncio
async def test_exact_inclusive_selected_tip_is_verified_without_a_false_partial_issue():
    selected = {
        "id": FUNGI_ID,
        "kingdom": "Fungi",
        "canonical_name": "Schizophyllum commune",
        "rank": "species",
        "lineage": ["Fungi", "Schizophyllum commune"],
        "lineage_ids": [ANCESTOR_ID, FUNGI_ID],
    }
    root = {
        "id": ANCESTOR_ID,
        "kingdom": "Fungi",
        "canonical_name": "Fungi",
        "rank": "kingdom",
    }
    db = Session(
        MappingsResult([selected]),
        MappingsResult([selected, root]),
    )

    result = await get_phylogeny(taxon_id=FUNGI_ID, db=db)
    nodes = flatten(result["tree"])

    assert [node["taxon_id"] for node in nodes] == [str(ANCESTOR_ID), str(FUNGI_ID)]
    assert [node["rank"] for node in nodes] == ["kingdom", "species"]
    assert result["status"] == "available"
    assert result["lineage_provenance"]["status"] == "available"
    assert result["lineage_provenance"]["raw_lineage"] == ["Fungi", "Schizophyllum commune"]
    assert result["lineage_provenance"]["raw_lineage_ids"] == [str(ANCESTOR_ID), str(FUNGI_ID)]
    assert result["lineage_provenance"]["issues"] == []


@pytest.mark.asyncio
async def test_inclusive_selected_name_with_wrong_uuid_remains_partial():
    wrong_id = UUID("fe11468f-e545-46be-b97d-3b1f5e4c86c6")
    selected = {
        "id": FUNGI_ID,
        "kingdom": "Fungi",
        "canonical_name": "Schizophyllum commune",
        "rank": "species",
        "lineage": ["Fungi", "Schizophyllum commune"],
        "lineage_ids": [ANCESTOR_ID, wrong_id],
    }
    root = {
        "id": ANCESTOR_ID,
        "kingdom": "Fungi",
        "canonical_name": "Fungi",
        "rank": "kingdom",
    }
    result = await get_phylogeny(
        taxon_id=FUNGI_ID,
        db=Session(MappingsResult([selected]), MappingsResult([root])),
    )

    assert flatten(result["tree"])[-1]["taxon_id"] == str(FUNGI_ID)
    assert result["status"] == "partial"
    assert any(
        issue == {"index": 1, "reason": "lineage_uuid_identity_mismatch"}
        for issue in result["lineage_provenance"]["issues"]
    )
