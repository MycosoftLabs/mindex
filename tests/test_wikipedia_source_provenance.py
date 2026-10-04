from __future__ import annotations

import hashlib
import json

from mindex_etl.sources.wikipedia import fetch_exact_page_summary, normalize_exact_page_summary


def summary(*, pageid=1473803, revision="1366763686", extract="A short source-provided summary."):
    return {
        "pageid": pageid,
        "revision": revision,
        "title": "Pleurotus ostreatus",
        "extract": extract,
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Pleurotus_ostreatus"}},
    }


def test_exact_wikipedia_candidate_pins_page_revision_and_hash_but_stays_unlicensed_candidate():
    candidate = normalize_exact_page_summary(
        summary(),
        source_url="https://en.wikipedia.org/wiki/Pleurotus_ostreatus",
        expected_page_id=1473803,
        source_content_sha256="a" * 64,
    )

    assert candidate["normalization_state"] == "candidate_source_revision_pinned"
    assert candidate["page_id"] == "1473803"
    assert candidate["revision_id"] == "1366763686"
    assert candidate["revision_url"] == "https://en.wikipedia.org/w/index.php?oldid=1366763686"
    assert candidate["attribution"] == "Wikipedia contributors"
    assert candidate["license"] is None
    assert candidate["license_state"] == "revision_pinned_license_unverified"
    assert candidate["published"] is False
    assert candidate["content_sha256"] == hashlib.sha256(b"A short source-provided summary.").hexdigest()
    assert len(candidate["normalization_sha256"]) == 64


def test_wikipedia_normalizer_rejects_wrong_page_revision_or_source_url():
    assert normalize_exact_page_summary(
        summary(pageid=11), source_url="https://en.wikipedia.org/wiki/Pleurotus_ostreatus", expected_page_id=1473803,
        source_content_sha256="a" * 64,
    )["state"] == "page_id_mismatch"
    assert normalize_exact_page_summary(
        summary(revision=""), source_url="https://en.wikipedia.org/wiki/Pleurotus_ostreatus", expected_page_id=1473803,
        source_content_sha256="a" * 64,
    )["state"] == "revision_id_missing"
    assert normalize_exact_page_summary(
        summary(), source_url="https://evil.example/wiki/Pleurotus_ostreatus", expected_page_id=1473803,
        source_content_sha256="a" * 64,
    )["state"] == "source_url_untrusted"
    assert normalize_exact_page_summary(
        summary(extract=""), source_url="https://en.wikipedia.org/wiki/Pleurotus_ostreatus", expected_page_id=1473803,
        source_content_sha256="a" * 64,
    )["state"] == "description_missing"


def test_fetcher_binds_article_url_to_expected_page_id(monkeypatch):
    class Response:
        status_code = 200
        content = json.dumps(summary()).encode()

        def raise_for_status(self):
            pass

        def json(self):
            return summary()

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    client = Client()
    result = fetch_exact_page_summary(
        "https://en.wikipedia.org/wiki/Pleurotus_ostreatus", expected_page_id=1473803, client=client,
    )

    assert result["revision_id"] == "1366763686"
    assert result["source_content_sha256"] == hashlib.sha256(Response.content).hexdigest()
    assert len(client.calls) == 1
    assert client.calls[0][0].endswith("/Pleurotus_ostreatus")
    assert fetch_exact_page_summary(
        "https://evil.example/wiki/Pleurotus_ostreatus", expected_page_id=1473803, client=client,
    ) == {"state": "source_url_untrusted"}
