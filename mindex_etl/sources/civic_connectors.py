"""Civic provider connectors for MINDEX unified viewport intelligence."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class CivicFetchResult:
    provider: str
    status: str
    records: list[dict[str, Any]]
    notes: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _http_json(url: str, headers: dict[str, str] | None = None, timeout: float = 8.0) -> Any:
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout) as response:  # nosec B310 - controlled URL set
        return json.loads(response.read().decode("utf-8"))


async def _http_json_async(url: str, headers: dict[str, str] | None = None, timeout: float = 8.0) -> Any:
    return await asyncio.to_thread(_http_json, url, headers, timeout)


async def fetch_google_civic(*, address: str) -> CivicFetchResult:
    api_key = os.environ.get("GOOGLE_CIVIC_API_KEY", "")
    if not api_key:
        return CivicFetchResult(provider="google_civic", status="missing_api_key", records=[])
    params = urlencode({"key": api_key, "address": address, "includeOffices": "true"})
    url = f"https://www.googleapis.com/civicinfo/v2/representatives?{params}"
    try:
        body = await _http_json_async(url, timeout=8.0)
        records: list[dict[str, Any]] = []
        officials = body.get("officials") or []
        offices = body.get("offices") or []
        for office in offices:
            for idx in office.get("officialIndices") or []:
                if idx >= len(officials):
                    continue
                official = officials[idx]
                records.append(
                    {
                        "name": official.get("name"),
                        "office": office.get("name") or "Official",
                        "party": official.get("party"),
                        "phones": official.get("phones") or [],
                        "emails": official.get("emails") or [],
                        "urls": official.get("urls") or [],
                        "image_url": official.get("photoUrl"),
                        "address": ", ".join(
                            [
                                str(x).strip()
                                for x in (
                                    ((official.get("address") or [{}])[0]).get("line1"),
                                    ((official.get("address") or [{}])[0]).get("city"),
                                    ((official.get("address") or [{}])[0]).get("state"),
                                    ((official.get("address") or [{}])[0]).get("zip"),
                                )
                                if x
                            ]
                        ) or None,
                        "provider_record": f"google_civic:{office.get('name','official')}:{official.get('name','')}",
                    }
                )
        return CivicFetchResult(provider="google_civic", status="live", records=records[:500])
    except Exception as exc:  # noqa: BLE001
        return CivicFetchResult(provider="google_civic", status="error", records=[], notes=str(exc)[:140])


async def fetch_data_gov_catalog(*, query: str) -> CivicFetchResult:
    api_key = os.environ.get("DATA_GOV_API_KEY", "")
    key_param = f"&api_key={api_key}" if api_key else ""
    url = f"https://catalog.data.gov/api/3/action/package_search?q={query}&rows=25{key_param}"
    try:
        body = await _http_json_async(url, timeout=8.0)
        rows = (body.get("result") or {}).get("results") or []
        records = [
            {
                "id": row.get("id"),
                "name": row.get("title") or row.get("name"),
                "office": "Data.gov Dataset",
                "urls": [row.get("url")] if row.get("url") else [],
                "provider_record": f"data_gov:{row.get('id','')}",
            }
            for row in rows
            if row.get("id")
        ]
        return CivicFetchResult(provider="data_gov", status="live", records=records)
    except Exception as exc:  # noqa: BLE001
        return CivicFetchResult(provider="data_gov", status="error", records=[], notes=str(exc)[:140])


async def fetch_legiscan(*, state_code: str | None = None) -> CivicFetchResult:
    api_key = os.environ.get("LEGISCAN_API_KEY", "")
    if not api_key:
        return CivicFetchResult(provider="legiscan", status="missing_api_key", records=[])
    # LegiScan op/state shape is API-plan dependent; use conservative endpoint.
    params = {"key": api_key, "op": "getMasterList"}
    if state_code:
        params["state"] = state_code
    url = f"https://api.legiscan.com/?{urlencode(params)}"
    try:
        body = await _http_json_async(url, timeout=10.0)
        master = body.get("masterlist") or {}
        records: list[dict[str, Any]] = []
        for _, row in master.items():
            if not isinstance(row, dict):
                continue
            records.append(
                {
                    "id": str(row.get("bill_id") or row.get("change_hash") or ""),
                    "name": row.get("title") or row.get("bill_number") or "Legislation",
                    "office": "Legislative Artifact",
                    "provider_record": f"legiscan:{row.get('bill_id','')}",
                    "source_url": row.get("url"),
                }
            )
        return CivicFetchResult(provider="legiscan", status="live", records=records[:300])
    except Exception as exc:  # noqa: BLE001
        return CivicFetchResult(provider="legiscan", status="error", records=[], notes=str(exc)[:140])


async def fetch_civicengine(*, lat: float, lng: float) -> CivicFetchResult:
    api_key = os.environ.get("CIVICENGINE_API_KEY", "")
    base_url = os.environ.get("CIVICENGINE_API_URL", "").strip()
    if not api_key or not base_url:
        return CivicFetchResult(provider="civicengine", status="missing_configuration", records=[])
    params = urlencode({"apikey": api_key, "lat": lat, "lng": lng})
    url = f"{base_url.rstrip('/')}/v1/officials?{params}"
    try:
        body = await _http_json_async(url, timeout=8.0)
        officials = body.get("officials") or body.get("results") or []
        records = [
            {
                "name": row.get("name"),
                "office": row.get("office") or "Official",
                "party": row.get("party"),
                "phones": [row.get("phone")] if row.get("phone") else [],
                "emails": [row.get("email")] if row.get("email") else [],
                "urls": [row.get("website")] if row.get("website") else [],
                "provider_record": f"civicengine:{row.get('id','')}",
            }
            for row in officials
            if isinstance(row, dict)
        ]
        return CivicFetchResult(provider="civicengine", status="live", records=records)
    except Exception as exc:  # noqa: BLE001
        return CivicFetchResult(provider="civicengine", status="error", records=[], notes=str(exc)[:140])


async def fetch_us_vote_foundation(*, state_code: str | None = None) -> CivicFetchResult:
    api_key = os.environ.get("US_VOTE_FOUNDATION_API_KEY", "")
    base_url = os.environ.get("US_VOTE_FOUNDATION_API_URL", "").strip()
    if not api_key or not base_url:
        return CivicFetchResult(provider="us_vote_foundation", status="missing_configuration", records=[])
    params = {"apikey": api_key}
    if state_code:
        params["state"] = state_code
    url = f"{base_url.rstrip('/')}/v1/elections?{urlencode(params)}"
    try:
        body = await _http_json_async(url, timeout=8.0)
        elections = body.get("elections") or body.get("results") or []
        records = [
            {
                "id": str(row.get("id") or ""),
                "name": row.get("name") or row.get("title") or "Election Event",
                "election_day": row.get("electionDay") or row.get("election_date"),
                "office": "Election Event",
                "provider_record": f"usvf:{row.get('id','')}",
                "source_url": row.get("url"),
            }
            for row in elections
            if isinstance(row, dict)
        ]
        return CivicFetchResult(provider="us_vote_foundation", status="live", records=records)
    except Exception as exc:  # noqa: BLE001
        return CivicFetchResult(provider="us_vote_foundation", status="error", records=[], notes=str(exc)[:140])


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_arcgis_point(geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(geometry, dict):
        return None
    x = _to_float(geometry.get("x"))
    y = _to_float(geometry.get("y"))
    if x is not None and y is not None:
        return (y, x)
    if isinstance(geometry.get("coordinates"), list) and len(geometry["coordinates"]) >= 2:
        lon = _to_float(geometry["coordinates"][0])
        lat = _to_float(geometry["coordinates"][1])
        if lat is not None and lon is not None:
            return (lat, lon)
    return None


def _bbox_tuple(north: float, south: float, east: float, west: float) -> str:
    return f"{west},{south},{east},{north}"


async def fetch_arcgis_hub_facilities(
    *,
    north: float,
    south: float,
    east: float,
    west: float,
    limit: int = 120,
) -> CivicFetchResult:
    """Fetch geolocated civic facilities from ArcGIS Hub collections."""
    host = os.environ.get("ARCGIS_CIVIC_HUB_URL", "https://chulavista-cvgis.opendata.arcgis.com").rstrip("/")
    search_url = f"{host}/api/search/v1?{urlencode({'q': 'facility', 'limit': 8})}"
    headers = {"Accept": "application/json", "User-Agent": "Mycosoft-MINDEX-CivicUnified/1.0"}
    try:
        search_body = await _http_json_async(search_url, headers=headers, timeout=10.0)
        candidates = search_body.get("results") or search_body.get("data") or []
        records: list[dict[str, Any]] = []
        per_collection = max(8, min(25, limit // max(1, len(candidates) or 1)))
        for row in candidates[:8]:
            collection_id = row.get("id") or row.get("collection", {}).get("id")
            if not collection_id:
                continue
            items_url = (
                f"{host}/api/search/v1/collections/{collection_id}/items?"
                + urlencode({"bbox": _bbox_tuple(north, south, east, west), "limit": per_collection})
            )
            try:
                items_body = await _http_json_async(items_url, headers=headers, timeout=10.0)
            except Exception:
                continue
            features = items_body.get("features") or items_body.get("results") or items_body.get("items") or []
            for feature in features:
                properties = feature.get("properties") or feature.get("attributes") or {}
                point = _extract_arcgis_point(feature.get("geometry"))
                if not point:
                    continue
                lat, lng = point
                name = (
                    properties.get("name")
                    or properties.get("facility_name")
                    or properties.get("title")
                    or feature.get("id")
                    or "Facility"
                )
                records.append(
                    {
                        "id": str(feature.get("id") or properties.get("objectid") or ""),
                        "name": str(name),
                        "office": "Facility",
                        "entity_type": "facility",
                        "type": properties.get("type") or properties.get("category") or "facility",
                        "lat": lat,
                        "lng": lng,
                        "agency": properties.get("agency") or properties.get("department"),
                        "phone": properties.get("phone") or properties.get("contact_phone"),
                        "email": properties.get("email") or properties.get("contact_email"),
                        "website": properties.get("website") or properties.get("url"),
                        "image_url": properties.get("image_url") or properties.get("photo_url"),
                        "source_url": items_url,
                        "provider_record": f"arcgis_hub:{collection_id}:{feature.get('id')}",
                    }
                )
                if len(records) >= limit:
                    break
            if len(records) >= limit:
                break
        return CivicFetchResult(provider="arcgis_hub", status="live", records=records[:limit])
    except Exception as exc:  # noqa: BLE001
        return CivicFetchResult(provider="arcgis_hub", status="error", records=[], notes=str(exc)[:140])

