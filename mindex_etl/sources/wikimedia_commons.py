"""Exact-file Wikimedia Commons metadata normalization; no media download or publish."""
from __future__ import annotations

import hashlib
import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import quote, urlsplit

import httpx


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parser = _TextOnly()
    parser.feed(value)
    text = " ".join(unescape(" ".join(parser.parts)).split())
    return text or None


def normalize_commons_file_candidate(payload: dict, *, expected_page_id: object, source_sha256: str) -> dict:
    page = (payload.get("query") or {}).get("pages") or {}
    if len(page) != 1:
        return {"state": "file_identity_missing"}
    record = next(iter(page.values()))
    page_id = str(record.get("pageid") or "")
    expected = str(expected_page_id or "")
    if not expected.isdigit() or page_id != expected:
        return {"state": "file_page_id_mismatch", "expected_page_id": expected or None, "returned_page_id": page_id or None}
    title = record.get("title")
    if not isinstance(title, str) or not title.startswith("File:"):
        return {"state": "file_title_invalid", "page_id": page_id}
    info_rows = record.get("imageinfo") or []
    if len(info_rows) != 1:
        return {"state": "file_imageinfo_missing_or_ambiguous", "page_id": page_id}
    info = info_rows[0]
    metadata = info.get("extmetadata") or {}
    license_name = _plain_text((metadata.get("LicenseShortName") or {}).get("value"))
    license_url = (metadata.get("LicenseUrl") or {}).get("value")
    artist = _plain_text((metadata.get("Artist") or {}).get("value"))
    attribution = _plain_text((metadata.get("Attribution") or {}).get("value"))
    if not attribution and artist and artist.casefold() not in {"see below", "voir ci-dessous / see below"}:
        attribution = artist
    image_url = info.get("url")
    image_parts = urlsplit(image_url) if isinstance(image_url, str) else None
    if not image_parts or image_parts.scheme != "https" or image_parts.netloc.casefold() != "upload.wikimedia.org":
        return {"state": "image_url_untrusted", "page_id": page_id}
    license_parts = urlsplit(license_url) if isinstance(license_url, str) else None
    if not license_parts or license_parts.scheme != "https" or license_parts.netloc.casefold() != "creativecommons.org":
        license_url = None
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256 or ""):
        return {"state": "source_hash_invalid", "page_id": page_id}
    source_file_url = info.get("descriptionurl") or (
        "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":()_')!,.-")
    )
    file_parts = urlsplit(source_file_url)
    if file_parts.scheme != "https" or file_parts.netloc.casefold() != "commons.wikimedia.org":
        return {"state": "source_file_url_untrusted", "page_id": page_id}
    result = {
        "provider": "wikimedia_commons",
        "source_file_page_id": page_id,
        "source_file_title": title,
        "source_file_url": source_file_url,
        "image_url": image_url,
        "license": license_name,
        "license_url": license_url,
        "creator": artist,
        "attribution": attribution,
        "source_content_sha256": source_sha256,
        "normalization_state": (
            "rights_metadata_recorded_review_required"
            if license_name and license_url and attribution
            else "withheld_missing_attribution_or_license"
        ),
        "published": False,
    }
    result["normalization_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return result


def fetch_commons_file_candidate(
    file_title: str,
    *,
    expected_page_id: object,
    client: Optional[httpx.Client] = None,
) -> dict:
    """Fetch one exact Commons file page and normalize its licensed metadata."""
    if not isinstance(file_title, str) or not file_title.startswith("File:") or not str(expected_page_id or "").isdigit():
        return {"state": "exact_file_identity_missing"}
    close_client = client is None
    client = client or httpx.Client()
    try:
        response = client.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "titles": file_title,
                "prop": "imageinfo", "iiprop": "url|extmetadata",
            },
            timeout=30,
            headers={"User-Agent": "MINDEX-ETL/1.0 (https://mycosoft.io; contact@mycosoft.org)"},
        )
        response.raise_for_status()
        payload = response.json()
        source_sha256 = hashlib.sha256(response.content).hexdigest()
        normalized = normalize_commons_file_candidate(
            payload, expected_page_id=expected_page_id, source_sha256=source_sha256,
        )
        if "normalization_state" in normalized:
            normalized["source_content_bytes"] = len(response.content)
            normalized_payload = {key: value for key, value in normalized.items() if key != "normalization_sha256"}
            normalized["normalization_sha256"] = hashlib.sha256(
                json.dumps(normalized_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        return normalized
    finally:
        if close_client:
            client.close()
