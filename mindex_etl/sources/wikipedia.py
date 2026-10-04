from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from typing import Dict, Optional
from urllib.parse import quote, unquote, urlsplit

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def fetch_page_summary(title: str, client: Optional[httpx.Client] = None) -> Dict:
    close_client = False
    if client is None:
        client = httpx.Client()
        close_client = True
    try:
        resp = client.get(
            f"{settings.wikipedia_api_url}/{quote(title)}",
            timeout=settings.http_timeout,
            headers={"User-Agent": "mindex-etl/0.1"},
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    finally:
        if close_client:
            client.close()


def normalize_exact_page_summary(
    summary: Dict, *, source_url: str, expected_page_id: object, source_content_sha256: str,
) -> dict:
    """Pin a Wikipedia extract to its exact page/revision without publishing it."""
    page_id = str(summary.get("pageid") or "")
    revision_id = str(summary.get("revision") or "")
    expected = str(expected_page_id or "")
    if not expected.isdigit() or page_id != expected:
        return {"state": "page_id_mismatch", "expected_page_id": expected or None, "returned_page_id": page_id or None}
    if not revision_id.isdigit():
        return {"state": "revision_id_missing", "page_id": page_id}
    parsed = urlsplit(source_url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != "en.wikipedia.org" or not parsed.path.startswith("/wiki/"):
        return {"state": "source_url_untrusted", "page_id": page_id}
    page_url = (summary.get("content_urls") or {}).get("desktop", {}).get("page")
    if not isinstance(page_url, str):
        return {"state": "canonical_page_url_missing", "page_id": page_id}
    canonical = urlsplit(page_url)
    if canonical.scheme != "https" or canonical.netloc.casefold() != "en.wikipedia.org" or not canonical.path.startswith("/wiki/"):
        return {"state": "canonical_page_url_untrusted", "page_id": page_id}
    extract = " ".join(unescape(str(summary.get("extract") or "")).split())
    if not extract:
        return {"state": "description_missing", "page_id": page_id, "revision_id": revision_id}
    content_hash = hashlib.sha256(extract.encode("utf-8")).hexdigest()
    revision_url = f"https://en.wikipedia.org/w/index.php?oldid={revision_id}"
    if not isinstance(source_content_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_content_sha256):
        return {"state": "source_hash_missing", "page_id": page_id, "revision_id": revision_id}
    normalized = {
        "provider": "wikipedia",
        "page_id": page_id,
        "revision_id": revision_id,
        "source_url": page_url,
        "revision_url": revision_url,
        "attribution": "Wikipedia contributors",
        "license": None,
        "license_policy_url": "https://en.wikipedia.org/wiki/Wikipedia:Copyrights",
        "license_state": "revision_pinned_license_unverified",
        "content_sha256": content_hash,
        "source_content_sha256": source_content_sha256,
        "normalization_state": "candidate_source_revision_pinned",
        "published": False,
        "text": extract,
    }
    normalized_payload = {key: value for key, value in normalized.items() if key != "text"}
    normalized["normalization_sha256"] = hashlib.sha256(
        json.dumps(normalized_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return normalized


def fetch_exact_page_summary(
    source_url: str,
    *,
    expected_page_id: object,
    client: Optional[httpx.Client] = None,
) -> dict:
    """Fetch an iNat-linked Wikipedia URL and require the expected page ID."""
    parsed = urlsplit(source_url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != "en.wikipedia.org" or not parsed.path.startswith("/wiki/"):
        return {"state": "source_url_untrusted"}
    title = unquote(parsed.path.removeprefix("/wiki/")).strip()
    if not title or not str(expected_page_id or "").isdigit():
        return {"state": "exact_page_identity_missing"}
    close_client = client is None
    client = client or httpx.Client()
    try:
        response = client.get(
            f"{settings.wikipedia_api_url}/{quote(title, safe='')}",
            timeout=settings.http_timeout,
            headers={"User-Agent": "MINDEX-ETL/1.0 (https://mycosoft.io; contact@mycosoft.org)"},
        )
        if response.status_code == 404:
            return {"state": "source_page_not_found", "expected_page_id": str(expected_page_id)}
        response.raise_for_status()
        payload = response.json()
        return normalize_exact_page_summary(
            payload,
            source_url=source_url,
            expected_page_id=expected_page_id,
            source_content_sha256=hashlib.sha256(response.content).hexdigest(),
        )
    finally:
        if close_client:
            client.close()


def extract_traits(summary: Dict) -> Dict[str, str]:
    traits = {}
    infobox = summary.get("infobox") or {}
    for key in ("ecology", "edibility", "cap shape", "hymenium type"):
        value = infobox.get(key)
        if value:
            traits[key] = value
    if summary.get("description"):
        traits["description"] = summary["description"]
    return traits
