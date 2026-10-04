from __future__ import annotations

import hashlib
import json
from uuid import UUID

from mindex_etl.sources.inat import map_inat_taxon
from mindex_etl.taxon_canonicalizer import upsert_taxon


def test_inat_wikipedia_summary_is_attributed_but_not_promoted_before_license_review():
    payload = map_inat_taxon({
        "id": 5322,
        "name": "Pleurotus ostreatus",
        "wikipedia_summary": "<p>Source description.</p>",
        "wikipedia_url": "https://en.wikipedia.org/wiki/Pleurotus_ostreatus",
    })

    candidate = payload["metadata"]["description_candidate"]
    assert payload["description"] is None
    assert candidate["source"] == "wikipedia_via_inaturalist_taxon"
    assert candidate["source_url"] == "https://en.wikipedia.org/wiki/Pleurotus_ostreatus"
    assert candidate["license"] is None
    assert candidate["license_state"] == "revision_specific_license_unverified"
    assert candidate["content_sha256"] == hashlib.sha256(b"Source description.").hexdigest()
    assert candidate["retrieved_at"].endswith("Z")
    assert candidate["transformations"] == ["HTML markup removed", "whitespace normalized"]


def test_inaturalist_taxon_description_is_not_treated_as_a_licensed_species_description():
    payload = map_inat_taxon({"id": 5322, "name": "Pleurotus ostreatus", "description": "Contributor text"})

    candidate = payload["metadata"]["description_candidate"]
    assert payload["description"] is None
    assert candidate["source"] == "inaturalist_taxon"
    assert candidate["license_state"] == "not_assessed_for_taxon_description"
    assert candidate["stored_as_species_description"] is False


class Cursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return {"id": UUID("c8814bf5-6317-4792-a8b2-5982404caa01"), "metadata": {
            "description_provenance": {"source": "curated", "state": "verified"},
            "gbif_id": "123",
        }}


class Connection:
    def __init__(self):
        self.cursor_value = Cursor()

    def cursor(self):
        return self.cursor_value


def test_taxon_upsert_preserves_existing_description_and_provenance_when_source_has_none():
    conn = Connection()
    taxon_id = upsert_taxon(
        conn,
        canonical_name="Pleurotus ostreatus",
        rank="species",
        source="inat",
        description=None,
        metadata={"inat_id": 5322, "description_candidate": {"license_state": "not_assessed"}},
    )

    assert str(taxon_id) == "c8814bf5-6317-4792-a8b2-5982404caa01"
    update_sql, params = conn.cursor_value.calls[1]
    assert "metadata = %s::jsonb" in update_sql
    updated_metadata = json.loads(params[-2])
    assert updated_metadata["description_provenance"] == {"source": "curated", "state": "verified"}
    assert updated_metadata["gbif_id"] == "123"
    assert updated_metadata["description_candidate"]["license_state"] == "not_assessed"
    assert "description =" not in update_sql
