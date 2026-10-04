from __future__ import annotations

import hashlib
import json

from mindex_etl.sources.wikimedia_commons import (
    fetch_commons_file_candidate,
    normalize_commons_file_candidate,
)


def payload(*, page_id=3330811, artist="Photographer", attribution="Photographer, CC BY 3.0"):
    return {"query": {"pages": {str(page_id): {
        "pageid": page_id,
        "title": "File:Pleurotus ostreatus JPG7.jpg",
        "imageinfo": [{
            "url": "https://upload.wikimedia.org/wikipedia/commons/f/f6/Pleurotus_ostreatus_JPG7.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Pleurotus_ostreatus_JPG7.jpg",
            "extmetadata": {
                "Artist": {"value": artist},
                "Attribution": {"value": attribution},
                "LicenseShortName": {"value": "CC BY 3.0"},
                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/3.0"},
            },
        }],
    }}}}


def test_commons_candidate_normalizes_exact_license_and_attribution_without_publishing():
    result = normalize_commons_file_candidate(payload(), expected_page_id=3330811, source_sha256="a" * 64)

    assert result["provider"] == "wikimedia_commons"
    assert result["source_file_page_id"] == "3330811"
    assert result["source_file_title"] == "File:Pleurotus ostreatus JPG7.jpg"
    assert result["license"] == "CC BY 3.0"
    assert result["license_url"] == "https://creativecommons.org/licenses/by/3.0"
    assert result["attribution"] == "Photographer, CC BY 3.0"
    assert result["normalization_state"] == "rights_metadata_recorded_review_required"
    assert result["published"] is False
    assert len(result["normalization_sha256"]) == 64


def test_commons_withholds_when_creator_is_unresolved_or_exact_page_id_differs():
    unresolved = normalize_commons_file_candidate(
        payload(artist="voir ci-dessous / see below", attribution=""),
        expected_page_id=3330811,
        source_sha256="b" * 64,
    )
    assert unresolved["license"] == "CC BY 3.0"
    assert unresolved["attribution"] is None
    assert unresolved["normalization_state"] == "withheld_missing_attribution_or_license"
    mismatch = normalize_commons_file_candidate(payload(page_id=8), expected_page_id=3330811, source_sha256="a" * 64)
    assert mismatch["state"] == "file_page_id_mismatch"


def test_commons_exact_fetch_uses_expected_file_id_and_hashes_response():
    body = json.dumps(payload()).encode()

    class Response:
        content = body

        def raise_for_status(self):
            pass

        def json(self):
            return json.loads(body)

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    client = Client()
    result = fetch_commons_file_candidate(
        "File:Pleurotus ostreatus JPG7.jpg", expected_page_id=3330811, client=client,
    )
    assert len(client.calls) == 1
    assert client.calls[0][1]["params"]["titles"] == "File:Pleurotus ostreatus JPG7.jpg"
    assert result["source_content_sha256"] == hashlib.sha256(body).hexdigest()
    assert result["source_content_bytes"] == len(body)
    assert result["normalization_state"] == "rights_metadata_recorded_review_required"
