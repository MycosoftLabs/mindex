"""MINDEX-first unified civic viewport intelligence endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any
import hashlib
import logging
import os

from fastapi import APIRouter, Depends, Query
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import asyncio
import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.civic_unified import (
    CivicElectionEvent,
    CivicFacility,
    CivicOffice,
    CivicProviderStatus,
    CivicRepresentative,
    CivicUnifiedViewportResponse,
    CivicViewportMeta,
    CivicViewportPlace,
    dedupe_key_for_office,
    dedupe_key_for_official,
    open_civic_division_id,
)
from ..dependencies import get_db_session
from mindex_etl.sources.civic_connectors import (
    fetch_arcgis_hub_facilities,
    fetch_civicengine,
    fetch_data_gov_catalog,
    fetch_google_civic,
    fetch_legiscan,
    fetch_us_vote_foundation,
)


router = APIRouter(prefix="/civic", tags=["Civic Unified"])
logger = logging.getLogger(__name__)


def _lod_from_zoom(zoom: float) -> str:
    return "country" if zoom < 5 else "state" if zoom < 8 else "county" if zoom < 11 else "city" if zoom < 14 else "facility"


def _cache_ttl_seconds(zoom: float) -> int:
    """Weekly cache — viewport intel is batch-refreshed by ETL, not live on pan."""
    _ = zoom
    return int(os.environ.get("CIVIC_VIEWPORT_CACHE_TTL_SECONDS", str(7 * 86400)))


def _allow_live_provider_refresh() -> bool:
    return os.environ.get("CIVIC_LIVE_PROVIDER_REFRESH", "0").strip().lower() in {"1", "true", "yes"}


def _allow_live_geocode() -> bool:
    return os.environ.get("CIVIC_LIVE_GEOCODE", "0").strip().lower() in {"1", "true", "yes"}


def _canonical_jurisdiction_key(place: CivicViewportPlace | None) -> str | None:
    if not place:
        return None
    return (
        f"{(place.country_code or '').lower()}|{(place.state or '').lower()}|"
        f"{(place.county or '').lower()}|{(place.city or '').lower()}"
    )


def _jurisdiction_keys_hierarchy(place: CivicViewportPlace | None) -> list[str]:
    if not place:
        return []
    cc = (place.country_code or "").lower()
    st = (place.state or "").lower()
    co = (place.county or "").lower()
    ci = (place.city or "").lower()
    keys: list[str] = []
    keys.append(f"{cc}|||")
    if st:
        keys.append(f"{cc}|{st}||")
    if co:
        keys.append(f"{cc}|{st}|{co}|")
    if ci:
        keys.append(f"{cc}|{st}|{co}|{ci}")
    return list(dict.fromkeys(keys))


def _cache_key(north: float, south: float, east: float, west: float, zoom: float) -> str:
    key = f"{north:.4f}|{south:.4f}|{east:.4f}|{west:.4f}|{zoom:.2f}|v1"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def _load_cached_payload(
    db: AsyncSession,
    key: str,
) -> dict[str, Any] | None:
    result = await db.execute(
        text(
            """
            SELECT payload
            FROM civic.viewport_cache
            WHERE cache_key = :cache_key
              AND expires_at > now()
            LIMIT 1
            """
        ),
        {"cache_key": key},
    )
    row = result.mappings().first()
    if not row:
        return None
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else None


async def _load_jurisdiction_cache_by_key(
    db: AsyncSession,
    jurisdiction_key: str,
    lod: str,
) -> dict[str, Any] | None:
    result = await db.execute(
        text(
            """
            SELECT payload
            FROM civic.viewport_cache
            WHERE jurisdiction_key = :jurisdiction_key
              AND expires_at > now()
            ORDER BY CASE WHEN lod = :lod THEN 0 ELSE 1 END, generated_at DESC
            LIMIT 1
            """
        ),
        {"jurisdiction_key": jurisdiction_key, "lod": lod},
    )
    row = result.mappings().first()
    if not row:
        return None
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else None


async def _resolve_place_from_mindex(
    db: AsyncSession,
    lat: float,
    lng: float,
) -> CivicViewportPlace | None:
    try:
        result = await db.execute(
            text(
                """
            SELECT
                display_name,
                country,
                country_code,
                state,
                county,
                city,
                ST_Y(centroid::geometry) AS lat,
                ST_X(centroid::geometry) AS lng,
                ST_Distance(
                    centroid,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                ) AS dist_m
            FROM civic.jurisdictions
            WHERE centroid IS NOT NULL
            ORDER BY dist_m ASC
            LIMIT 1
            """
            ),
            {"lat": lat, "lng": lng},
        )
        row = result.mappings().first()
        if not row:
            return None
        return CivicViewportPlace(
            display_name=row.get("display_name"),
            country=row.get("country"),
            country_code=(row.get("country_code") or "").upper() or None,
            state=row.get("state"),
            county=row.get("county"),
            city=row.get("city"),
            lat=float(lat),
            lng=float(lng),
        )
    except Exception:
        await db.rollback()
        logger.debug("civic.jurisdictions unavailable for place resolution")
        return None


async def _load_officials_from_canonical_db(
    db: AsyncSession,
    jurisdiction_keys: list[str],
) -> list[CivicRepresentative]:
    if not jurisdiction_keys:
        return []
    try:
        result = await db.execute(
            text(
                """
            SELECT
                o.id::text AS id,
                o.name,
                o.office,
                o.level,
                o.party,
                o.image_url,
                o.source,
                j.display_name AS jurisdiction_name,
                j.open_civic_division_id
            FROM civic.officials o
            LEFT JOIN civic.jurisdictions j ON o.jurisdiction_id = j.id
            WHERE j.canonical_key = ANY(:keys)
            ORDER BY o.confidence_score DESC NULLS LAST, o.name ASC
            LIMIT 120
            """
            ),
            {"keys": jurisdiction_keys},
        )
        rows = result.mappings().all()
        if not rows:
            return []
        official_ids = [str(row["id"]) for row in rows if row.get("id")]
        contacts_by_official: dict[str, dict[str, list[str] | str | None]] = {}
        if official_ids:
            contact_result = await db.execute(
                text(
                    """
                SELECT official_id::text AS official_id, contact_type, contact_value
                FROM civic.official_contacts
                WHERE official_id::text = ANY(:ids)
                """
                ),
                {"ids": official_ids},
            )
            for contact in contact_result.mappings().all():
                oid = str(contact.get("official_id") or "")
                bucket = contacts_by_official.setdefault(
                    oid,
                    {"phones": [], "emails": [], "urls": [], "address": None},
                )
                ctype = str(contact.get("contact_type") or "").lower()
                value = str(contact.get("contact_value") or "").strip()
                if not value:
                    continue
                if ctype == "phone":
                    bucket["phones"].append(value)
                elif ctype == "email":
                    bucket["emails"].append(value)
                elif ctype == "website":
                    bucket["urls"].append(value)
                elif ctype == "address":
                    bucket["address"] = value
        representatives: list[CivicRepresentative] = []
        for idx, row in enumerate(rows, start=1):
            oid = str(row.get("id") or idx)
            contacts = contacts_by_official.get(oid, {})
            representatives.append(
                CivicRepresentative(
                    id=f"official:{oid}",
                    name=str(row.get("name") or "Official"),
                    office=str(row.get("office") or "Office"),
                    level=row.get("level"),
                    party=row.get("party"),
                    jurisdiction_name=row.get("jurisdiction_name"),
                    open_civic_division_id=row.get("open_civic_division_id"),
                    phones=list(contacts.get("phones") or []),
                    emails=list(contacts.get("emails") or []),
                    urls=list(contacts.get("urls") or []),
                    address=contacts.get("address") if isinstance(contacts.get("address"), str) else None,
                    image_url=row.get("image_url"),
                    provider_records=[str(row.get("source") or "mindex:civic.officials")],
                )
            )
        return representatives
    except Exception:
        await db.rollback()
        logger.debug("civic.officials unavailable")
        return []


async def _load_elections_from_canonical_db(
    db: AsyncSession,
    jurisdiction_keys: list[str],
    place: CivicViewportPlace | None,
) -> list[CivicElectionEvent]:
    if not jurisdiction_keys:
        return []
    result = await db.execute(
        text(
            """
            SELECT
                e.id::text AS id,
                e.name,
                e.election_day::text AS election_day,
                e.source_url,
                e.source,
                j.display_name AS jurisdiction_name,
                j.open_civic_division_id
            FROM civic.elections e
            LEFT JOIN civic.jurisdictions j ON e.jurisdiction_id = j.id
            WHERE j.canonical_key = ANY(:keys)
            ORDER BY e.election_day DESC NULLS LAST, e.name ASC
            LIMIT 60
            """
        ),
        {"keys": jurisdiction_keys},
    )
    elections: list[CivicElectionEvent] = []
    for idx, row in enumerate(result.mappings().all(), start=1):
        elections.append(
            CivicElectionEvent(
                id=f"election:{row.get('id') or idx}",
                name=str(row.get("name") or "Election"),
                election_day=row.get("election_day"),
                jurisdiction_name=row.get("jurisdiction_name")
                or ((place.city if place else None) or (place.county if place else None) or (place.state if place else None)),
                open_civic_division_id=row.get("open_civic_division_id"),
                source_url=row.get("source_url"),
                provider_records=[str(row.get("source") or "mindex:civic.elections")],
            )
        )
    return elections


async def _load_civic_facilities_from_canonical_db(
    db: AsyncSession,
    north: float,
    south: float,
    east: float,
    west: float,
    limit: int = 350,
) -> list[CivicFacility]:
    try:
        result = await db.execute(
            text(
                """
                SELECT
                    f.id::text AS id,
                    f.name,
                    f.facility_type,
                    ST_Y(f.position::geometry) AS lat,
                    ST_X(f.position::geometry) AS lng,
                    f.agency,
                    f.source,
                    f.metadata,
                    (
                        SELECT fi.image_url
                        FROM civic.facility_images fi
                        WHERE fi.facility_id = f.id
                        ORDER BY fi.created_at DESC
                        LIMIT 1
                    ) AS image_url
                FROM civic.facilities f
                WHERE f.position IS NOT NULL
                  AND ST_Intersects(
                    f.position,
                    ST_MakeEnvelope(:west, :south, :east, :north, 4326)::geography
                  )
                ORDER BY f.updated_at DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {"north": north, "south": south, "east": east, "west": west, "limit": limit},
        )
        facilities: list[CivicFacility] = []
        for row in result.mappings().all():
            lat = row.get("lat")
            lng = row.get("lng")
            if lat is None or lng is None:
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            facilities.append(
                CivicFacility(
                    id=f"facility:{row.get('id')}",
                    name=str(row.get("name") or "Facility"),
                    type=str(row.get("facility_type") or "facility"),
                    lat=float(lat),
                    lng=float(lng),
                    agency=row.get("agency") or metadata.get("agency"),
                    phone=metadata.get("phone"),
                    email=metadata.get("email"),
                    website=metadata.get("website"),
                    image_url=row.get("image_url") or metadata.get("image_url"),
                    source=str(row.get("source") or "mindex:civic.facilities"),
                )
            )
        return facilities
    except Exception:
        await db.rollback()
        logger.exception("Failed loading civic facilities from civic.facilities")
        return []


def _has_sufficient_canonical_data(
    representatives: list[CivicRepresentative],
    elections: list[CivicElectionEvent],
    facilities: list[CivicFacility],
) -> bool:
    return bool(representatives or elections or facilities)


async def _store_cached_payload(
    db: AsyncSession,
    key: str,
    north: float,
    south: float,
    east: float,
    west: float,
    zoom: float,
    lod: str,
    place_name: str | None,
    jurisdiction_key: str | None,
    payload: dict[str, Any],
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO civic.viewport_cache (
                cache_key, north, south, east, west, zoom, lod, place_name, jurisdiction_key, payload, generated_at, expires_at
            ) VALUES (
                :cache_key, :north, :south, :east, :west, :zoom, :lod, :place_name, :jurisdiction_key,
                CAST(:payload AS jsonb), now(), now() + make_interval(secs => :ttl_seconds)
            )
            ON CONFLICT (cache_key) DO UPDATE SET
                north = EXCLUDED.north,
                south = EXCLUDED.south,
                east = EXCLUDED.east,
                west = EXCLUDED.west,
                zoom = EXCLUDED.zoom,
                lod = EXCLUDED.lod,
                place_name = EXCLUDED.place_name,
                jurisdiction_key = EXCLUDED.jurisdiction_key,
                payload = EXCLUDED.payload,
                generated_at = now(),
                expires_at = now() + make_interval(secs => :ttl_seconds)
            """
        ),
        {
            "cache_key": key,
            "north": north,
            "south": south,
            "east": east,
            "west": west,
            "zoom": zoom,
            "lod": lod,
            "place_name": place_name,
            "jurisdiction_key": jurisdiction_key,
            "payload": json.dumps(payload),
            "ttl_seconds": _cache_ttl_seconds(zoom),
        },
    )


async def _persist_canonical_snapshot(
    db: AsyncSession,
    place: CivicViewportPlace | None,
    representatives: list[CivicRepresentative],
    elections: list[CivicElectionEvent],
    facilities: list[CivicFacility],
    provider_status: list[CivicProviderStatus],
) -> None:
    if not place:
        return
    jurisdiction_key = (
        f"{(place.country_code or '').lower()}|{(place.state or '').lower()}|"
        f"{(place.county or '').lower()}|{(place.city or '').lower()}"
    )
    centroid_lat = place.lat
    centroid_lng = place.lng
    await db.execute(
        text(
            """
            INSERT INTO civic.jurisdictions (
                canonical_key, country, country_code, state, county, city, open_civic_division_id, display_name, centroid, updated_at
            ) VALUES (
                :canonical_key, :country, :country_code, :state, :county, :city, :division_id, :display_name,
                CASE
                    WHEN :lng IS NOT NULL AND :lat IS NOT NULL
                    THEN ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                    ELSE NULL::geography
                END,
                now()
            )
            ON CONFLICT (canonical_key) DO UPDATE SET
                country = EXCLUDED.country,
                country_code = EXCLUDED.country_code,
                state = EXCLUDED.state,
                county = EXCLUDED.county,
                city = EXCLUDED.city,
                open_civic_division_id = COALESCE(EXCLUDED.open_civic_division_id, civic.jurisdictions.open_civic_division_id),
                display_name = COALESCE(EXCLUDED.display_name, civic.jurisdictions.display_name),
                centroid = COALESCE(EXCLUDED.centroid, civic.jurisdictions.centroid),
                updated_at = now()
            """
        ),
        {
            "canonical_key": jurisdiction_key,
            "country": place.country,
            "country_code": place.country_code,
            "state": place.state,
            "county": place.county,
            "city": place.city,
            "division_id": open_civic_division_id(
                country_code=place.country_code,
                state=place.state,
                county=place.county,
                city=place.city,
            ),
            "display_name": place.display_name,
            "lat": centroid_lat,
            "lng": centroid_lng,
        },
    )
    for rep in representatives:
        official_key = f"{(rep.open_civic_division_id or '').lower()}|{rep.office.lower()}|{rep.name.lower()}"
        await db.execute(
            text(
                """
                INSERT INTO civic.officials (
                    canonical_key, jurisdiction_id, name, office, level, party, image_url, source, confidence_score, updated_at
                ) VALUES (
                    :canonical_key,
                    (SELECT id FROM civic.jurisdictions WHERE canonical_key = :jurisdiction_key),
                    :name, :office, :level, :party, :image_url, :source, :confidence_score, now()
                )
                ON CONFLICT (canonical_key) DO UPDATE SET
                    jurisdiction_id = COALESCE(EXCLUDED.jurisdiction_id, civic.officials.jurisdiction_id),
                    name = EXCLUDED.name,
                    office = EXCLUDED.office,
                    level = EXCLUDED.level,
                    party = COALESCE(EXCLUDED.party, civic.officials.party),
                    image_url = COALESCE(EXCLUDED.image_url, civic.officials.image_url),
                    source = COALESCE(EXCLUDED.source, civic.officials.source),
                    confidence_score = GREATEST(civic.officials.confidence_score, EXCLUDED.confidence_score),
                    updated_at = now()
                """
            ),
            {
                "canonical_key": official_key,
                "jurisdiction_key": jurisdiction_key,
                "name": rep.name,
                "office": rep.office,
                "level": rep.level,
                "party": rep.party,
                "image_url": rep.image_url,
                "source": rep.provider_records[0] if rep.provider_records else "provider",
                "confidence_score": 0.85,
            },
        )
        contact_values: list[tuple[str, str | None, float]] = []
        contact_values.extend([("phone", value, 0.85) for value in rep.phones if value])
        contact_values.extend([("email", value, 0.9) for value in rep.emails if value])
        contact_values.extend([("website", value, 0.8) for value in rep.urls if value])
        if rep.address:
            contact_values.append(("address", rep.address, 0.78))
        for contact_type, contact_value, _confidence in contact_values:
            await db.execute(
                text(
                    """
                    INSERT INTO civic.official_contacts (
                        official_id, contact_type, contact_value, source, created_at
                    ) VALUES (
                        (SELECT id FROM civic.officials WHERE canonical_key = :official_key),
                        :contact_type, :contact_value, :source, now()
                    )
                    ON CONFLICT (official_id, contact_type, contact_value) DO UPDATE SET
                        source = COALESCE(EXCLUDED.source, civic.official_contacts.source)
                    """
                ),
                {
                    "official_key": official_key,
                    "contact_type": contact_type,
                    "contact_value": contact_value,
                    "source": rep.provider_records[0] if rep.provider_records else "provider",
                },
            )
    for election in elections:
        election_key = f"{(election.open_civic_division_id or '').lower()}|{election.name.lower()}|{(election.election_day or '').lower()}"
        await db.execute(
            text(
                """
                INSERT INTO civic.elections (
                    canonical_key, jurisdiction_id, name, election_day, source_url, source, confidence_score, updated_at
                ) VALUES (
                    :canonical_key,
                    (SELECT id FROM civic.jurisdictions WHERE canonical_key = :jurisdiction_key),
                    :name, NULLIF(:election_day, '')::date, :source_url, :source, :confidence_score, now()
                )
                ON CONFLICT (canonical_key) DO UPDATE SET
                    jurisdiction_id = COALESCE(EXCLUDED.jurisdiction_id, civic.elections.jurisdiction_id),
                    name = EXCLUDED.name,
                    election_day = COALESCE(EXCLUDED.election_day, civic.elections.election_day),
                    source_url = COALESCE(EXCLUDED.source_url, civic.elections.source_url),
                    source = COALESCE(EXCLUDED.source, civic.elections.source),
                    confidence_score = GREATEST(civic.elections.confidence_score, EXCLUDED.confidence_score),
                    updated_at = now()
                """
            ),
            {
                "canonical_key": election_key,
                "jurisdiction_key": jurisdiction_key,
                "name": election.name,
                "election_day": election.election_day or "",
                "source_url": election.source_url,
                "source": election.provider_records[0] if election.provider_records else "provider",
                "confidence_score": 0.8,
            },
        )
    for facility in facilities:
        facility_key = f"{facility.type.lower()}|{facility.name.lower()}|{round(facility.lat, 4)}|{round(facility.lng, 4)}"
        await db.execute(
            text(
                """
                INSERT INTO civic.facilities (
                    canonical_key, jurisdiction_id, name, facility_type, position, agency, source, confidence_score, metadata, updated_at
                ) VALUES (
                    :canonical_key,
                    (SELECT id FROM civic.jurisdictions WHERE canonical_key = :jurisdiction_key),
                    :name, :facility_type,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                    :agency, :source, :confidence_score, CAST(:metadata AS jsonb), now()
                )
                ON CONFLICT (canonical_key) DO UPDATE SET
                    jurisdiction_id = COALESCE(EXCLUDED.jurisdiction_id, civic.facilities.jurisdiction_id),
                    name = EXCLUDED.name,
                    facility_type = EXCLUDED.facility_type,
                    position = COALESCE(EXCLUDED.position, civic.facilities.position),
                    agency = COALESCE(EXCLUDED.agency, civic.facilities.agency),
                    source = COALESCE(EXCLUDED.source, civic.facilities.source),
                    confidence_score = GREATEST(civic.facilities.confidence_score, EXCLUDED.confidence_score),
                    metadata = civic.facilities.metadata || EXCLUDED.metadata,
                    updated_at = now()
                """
            ),
            {
                "canonical_key": facility_key,
                "jurisdiction_key": jurisdiction_key,
                "name": facility.name,
                "facility_type": facility.type,
                "lat": facility.lat,
                "lng": facility.lng,
                "agency": facility.agency,
                "source": facility.source,
                "confidence_score": 0.82,
                "metadata": json.dumps(
                    {
                        "phone": facility.phone,
                        "email": facility.email,
                        "website": facility.website,
                        "image_url": facility.image_url,
                        "provider_records": [facility.source],
                    }
                ),
            },
        )
        if facility.image_url:
            await db.execute(
                text(
                    """
                    INSERT INTO civic.facility_images (
                        facility_id, image_url, source, license, attribution, usage_rights, created_at
                    ) VALUES (
                        (SELECT id FROM civic.facilities WHERE canonical_key = :facility_key),
                        :image_url, :source, 'unknown', :attribution, NULL, now()
                    )
                    ON CONFLICT (facility_id, image_url) DO UPDATE SET
                        source = COALESCE(EXCLUDED.source, civic.facility_images.source),
                        attribution = COALESCE(EXCLUDED.attribution, civic.facility_images.attribution),
                        license = COALESCE(EXCLUDED.license, civic.facility_images.license),
                        usage_rights = COALESCE(EXCLUDED.usage_rights, civic.facility_images.usage_rights)
                    """
                ),
                {
                    "facility_key": facility_key,
                    "image_url": facility.image_url,
                    "source": facility.source,
                    "attribution": facility.source,
                },
            )
    for status in provider_status:
        await db.execute(
            text(
                """
                INSERT INTO civic.source_lineage (
                    entity_type, entity_key, source_name, source_record_id, fetched_at, confidence_score, metadata
                ) VALUES (
                    'viewport', :entity_key, :source_name, :source_record_id, now(), :confidence_score, CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "entity_key": jurisdiction_key,
                "source_name": status.provider,
                "source_record_id": status.provider,
                "confidence_score": 0.8,
                "metadata": json.dumps({"status": status.status, "records": status.records, "notes": status.notes}),
            },
        )


def _http_json(url: str, headers: dict[str, str] | None = None, timeout: float = 8.0) -> Any:
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout) as response:  # nosec B310 - controlled URL set
        return json.loads(response.read().decode("utf-8"))


async def _http_json_async(url: str, headers: dict[str, str] | None = None, timeout: float = 8.0) -> Any:
    return await asyncio.to_thread(_http_json, url, headers, timeout)


def _bbox_area(north: float, south: float, east: float, west: float) -> float:
    lat_span = max(0.001, abs(north - south))
    lng_span = max(0.001, east - west if east >= west else 360 - west + east)
    return lat_span * lng_span


def _center(north: float, south: float, east: float, west: float) -> tuple[float, float]:
    lat = (north + south) / 2
    lng = (east + west) / 2
    if west > east:
        lng = ((east + 360 + west) / 2) % 360
    if lng > 180:
        lng -= 360
    return lat, lng


async def _reverse_place(north: float, south: float, east: float, west: float) -> CivicViewportPlace | None:
    lat, lng = _center(north, south, east, west)
    zoom = "4" if _bbox_area(north, south, east, west) > 70 else "6" if _bbox_area(north, south, east, west) > 10 else "10"
    params = urlencode({"format": "jsonv2", "addressdetails": "1", "lat": lat, "lon": lng, "zoom": zoom})
    url = f"https://nominatim.openstreetmap.org/reverse?{params}"
    try:
        body = await _http_json_async(
            url,
            headers={
                "User-Agent": "Mycosoft-MINDEX-CivicUnified/1.0 (ops@mycosoft.com)",
                "Accept": "application/json",
            },
            timeout=8.0,
        )
        a = body.get("address") or {}
        return CivicViewportPlace(
            display_name=body.get("display_name"),
            country=a.get("country"),
            country_code=(a.get("country_code") or "").upper() or None,
            state=a.get("state") or a.get("region"),
            county=a.get("county"),
            city=a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or a.get("hamlet"),
            suburb=a.get("suburb") or a.get("neighbourhood"),
            postcode=a.get("postcode"),
            lat=lat,
            lng=lng,
        )
    except Exception:  # noqa: BLE001
        return None


def _dedupe_representatives(
    records: list[dict[str, Any]],
    place: CivicViewportPlace | None,
    precedence: list[str],
) -> list[CivicRepresentative]:
    division_id = open_civic_division_id(
        country_code=place.country_code if place else None,
        state=place.state if place else None,
        county=place.county if place else None,
        city=place.city if place else None,
    )
    source_rank = {name: idx for idx, name in enumerate(precedence)}
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        provider = str(record.get("_provider") or "unknown")
        payload = {
            "name": str(record.get("name") or "").strip(),
            "office": str(record.get("office") or "Official").strip(),
            "party": record.get("party"),
            "phones": list(record.get("phones") or []),
            "emails": list(record.get("emails") or []),
            "urls": list(record.get("urls") or []),
            "address": record.get("address"),
            "image_url": record.get("image_url"),
            "open_civic_division_id": record.get("open_civic_division_id") or division_id,
            "provider_records": [str(record.get("provider_record") or provider)],
            "_provider": provider,
        }
        if not payload["name"]:
            continue
        key = dedupe_key_for_official(payload)
        existing = deduped.get(key)
        if not existing:
            deduped[key] = payload
            continue
        if source_rank.get(provider, 999) < source_rank.get(existing.get("_provider", ""), 999):
            existing["_provider"] = provider
        existing["provider_records"] = sorted(set(existing["provider_records"] + payload["provider_records"]))
        for field in ("party", "address"):
            if not existing.get(field) and payload.get(field):
                existing[field] = payload[field]
        if not existing.get("image_url") and payload.get("image_url"):
            existing["image_url"] = payload["image_url"]
        for field in ("phones", "emails", "urls"):
            existing[field] = sorted(set(list(existing.get(field) or []) + list(payload.get(field) or [])))
    return [
        CivicRepresentative(
            id=f"official:{idx}",
            name=v["name"],
            office=v["office"],
            level="government",
            party=v.get("party"),
            jurisdiction_name=(place.city if place else None) or (place.county if place else None) or (place.state if place else None),
            open_civic_division_id=v.get("open_civic_division_id"),
            phones=v.get("phones") or [],
            emails=v.get("emails") or [],
            urls=v.get("urls") or [],
            address=v.get("address"),
            image_url=v.get("image_url"),
            provider_records=v.get("provider_records") or [],
        )
        for idx, v in enumerate(deduped.values(), start=1)
    ]


def _offices_from_reps(reps: list[CivicRepresentative]) -> list[CivicOffice]:
    deduped: dict[str, CivicOffice] = {}
    for rep in reps:
        payload = {
            "name": rep.office,
            "open_civic_division_id": rep.open_civic_division_id,
        }
        key = dedupe_key_for_office(payload)
        if key in deduped:
            deduped[key].provider_records = sorted(set(deduped[key].provider_records + rep.provider_records))
            continue
        deduped[key] = CivicOffice(
            id=f"office:{len(deduped) + 1}",
            name=rep.office,
            level=rep.level,
            jurisdiction_name=rep.jurisdiction_name,
            open_civic_division_id=rep.open_civic_division_id,
            provider_records=sorted(set(rep.provider_records)),
        )
    return list(deduped.values())


def _elections_from_records(records: list[dict[str, Any]], place: CivicViewportPlace | None) -> list[CivicElectionEvent]:
    division_id = open_civic_division_id(
        country_code=place.country_code if place else None,
        state=place.state if place else None,
        county=place.county if place else None,
        city=place.city if place else None,
    )
    deduped: dict[str, dict[str, Any]] = {}
    for row in records:
        election_id = str(row.get("id") or row.get("name") or "").strip()
        if not election_id:
            continue
        if election_id not in deduped:
            deduped[election_id] = {
                "id": election_id,
                "name": row.get("name") or "Election Event",
                "election_day": row.get("election_day"),
                "source_url": row.get("source_url"),
                "provider_records": [str(row.get("provider_record") or row.get("_provider") or "provider")],
            }
            continue
        deduped[election_id]["provider_records"] = sorted(
            set(deduped[election_id]["provider_records"] + [str(row.get("provider_record") or row.get("_provider") or "provider")])
        )
        if not deduped[election_id].get("election_day") and row.get("election_day"):
            deduped[election_id]["election_day"] = row.get("election_day")
    return [
        CivicElectionEvent(
            id=f"election:{idx}",
            name=item["name"],
            election_day=item.get("election_day"),
            jurisdiction_name=(place.city if place else None) or (place.county if place else None) or (place.state if place else None),
            open_civic_division_id=division_id,
            source_url=item.get("source_url"),
            provider_records=item.get("provider_records", []),
        )
        for idx, item in enumerate(deduped.values(), start=1)
    ]


async def _load_facilities_from_mindex(
    db: AsyncSession,
    north: float,
    south: float,
    east: float,
    west: float,
    limit: int = 250,
) -> list[CivicFacility]:
    try:
        result = await db.execute(
            text(
                """
                SELECT
                    id::text AS id,
                    name,
                    ST_Y(location::geometry) AS lat,
                    ST_X(location::geometry) AS lng
                FROM infra.facilities
                WHERE location IS NOT NULL
                  AND ST_Intersects(location, ST_MakeEnvelope(:west, :south, :east, :north, 4326)::geography)
                ORDER BY id DESC
                LIMIT :limit
                """
            ),
            {
                "north": north,
                "south": south,
                "east": east,
                "west": west,
                "limit": limit,
            },
        )
        facilities: list[CivicFacility] = []
        for row in result.mappings().all():
            lat = row.get("lat")
            lng = row.get("lng")
            if lat is None or lng is None:
                continue
            metadata: dict[str, Any] = {}
            facility_type = "facility"
            facilities.append(
                CivicFacility(
                    id=f"facility:{row.get('id')}",
                    name=str(row.get("name") or "Facility"),
                    type=facility_type,
                    lat=float(lat),
                    lng=float(lng),
                    agency=(metadata.get("agency") if isinstance(metadata, dict) else None),
                    phone=(metadata.get("phone") if isinstance(metadata, dict) else None),
                    email=(metadata.get("email") if isinstance(metadata, dict) else None),
                    website=(metadata.get("website") if isinstance(metadata, dict) else None),
                    source="mindex:infra.facilities",
                )
            )
        return facilities
    except Exception:
        await db.rollback()
        logger.exception("Failed loading civic facilities from infra.facilities")
        return []


def _facilities_from_provider_records(
    records: list[dict[str, Any]],
    *,
    default_source: str,
) -> list[CivicFacility]:
    facilities: list[CivicFacility] = []
    for idx, row in enumerate(records, start=1):
        lat = row.get("lat")
        lng = row.get("lng")
        if lat is None or lng is None:
            continue
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (TypeError, ValueError):
            continue
        facilities.append(
            CivicFacility(
                id=f"facility:{row.get('id') or idx}",
                name=str(row.get("name") or "Facility"),
                type=str(row.get("type") or "facility"),
                lat=lat_f,
                lng=lng_f,
                agency=row.get("agency"),
                phone=row.get("phone"),
                email=row.get("email"),
                website=row.get("website"),
                image_url=row.get("image_url"),
                source=str(row.get("_provider") or default_source),
            )
        )
    return facilities


    return facilities


def _build_viewport_response_payload(
    *,
    place: CivicViewportPlace | None,
    n: float,
    s: float,
    e: float,
    w: float,
    zoom: float,
    lod: str,
    center_lat: float,
    center_lng: float,
    representatives: list[CivicRepresentative],
    elections: list[CivicElectionEvent],
    facilities: list[CivicFacility],
    legislation_records: list[dict[str, Any]],
    provider_status: list[CivicProviderStatus],
    provider_counts: dict[str, int],
    started: float,
    source_lineage_note: str = "mindex",
) -> CivicUnifiedViewportResponse:
    offices = _offices_from_reps(representatives)
    jurisdiction_name = (place.city if place else None) or (place.county if place else None) or (place.state if place else None)
    officials_panel = [
        {
            "id": rep.id,
            "name": rep.name,
            "office": rep.office,
            "jurisdiction_name": rep.jurisdiction_name,
            "image_url": rep.image_url,
            "contacts": {
                "phones": rep.phones,
                "emails": rep.emails,
                "urls": rep.urls,
                "address": rep.address,
            },
            "source": rep.provider_records[0] if rep.provider_records else source_lineage_note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "confidence_score": 0.85,
        }
        for rep in representatives
    ]
    facilities_panel = [
        {
            "id": facility.id,
            "name": facility.name,
            "geo": {"lat": facility.lat, "lng": facility.lng},
            "type": facility.type,
            "agency": facility.agency,
            "image_url": facility.image_url,
            "contacts": {
                "phone": facility.phone,
                "email": facility.email,
                "website": facility.website,
            },
            "source": facility.source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "confidence_score": 0.75,
        }
        for facility in facilities
    ]
    legislation_panel = [
        {
            "id": str(item.get("id") or f"bill:{idx}"),
            "name": item.get("name") or "Legislation",
            "status": item.get("status") or "tracked",
            "source_url": item.get("source_url"),
            "source": item.get("_provider") or source_lineage_note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "confidence_score": 0.78,
        }
        for idx, item in enumerate(legislation_records[:100], start=1)
    ]
    media_gallery = [
        {
            "entity_type": "official",
            "entity_id": official["id"],
            "image_url": official.get("image_url"),
            "license": "unknown",
            "attribution": official.get("source"),
            "source": official.get("source"),
        }
        for official in officials_panel
        if official.get("image_url")
    ] + [
        {
            "entity_type": "facility",
            "entity_id": facility["id"],
            "image_url": facility.get("image_url"),
            "license": "unknown",
            "attribution": facility.get("source"),
            "source": facility.get("source"),
        }
        for facility in facilities_panel
        if facility.get("image_url")
    ]
    jurisdiction_stack = [
        {"level": "country", "name": place.country, "code": place.country_code} if place and place.country else None,
        {"level": "state", "name": place.state} if place and place.state else None,
        {"level": "county", "name": place.county} if place and place.county else None,
        {"level": "city", "name": place.city} if place and place.city else None,
    ]
    jurisdiction_stack = [item for item in jurisdiction_stack if item]
    total_ms = int((perf_counter() - started) * 1000)
    meta = CivicViewportMeta(
        source_lineage=provider_status
        or [
            CivicProviderStatus(
                provider=source_lineage_note,
                status="cached",
                fetched_at=datetime.now(timezone.utc),
                records=len(representatives) + len(elections) + len(facilities),
                notes="MINDEX canonical read path",
            )
        ],
        dedupe_confidence=0.98 if representatives else 0.75,
        freshness_utc=datetime.now(timezone.utc).isoformat(),
        total_ms=total_ms,
        within_budget=total_ms <= 1200,
        provider_counts=provider_counts,
    )
    return CivicUnifiedViewportResponse(
        ok=True,
        generated_at=datetime.now(timezone.utc).isoformat(),
        lod=lod,
        bounds={"north": n, "south": s, "east": e, "west": w},
        center={"lat": center_lat, "lng": center_lng},
        place=place,
        representatives=representatives[:50],
        offices=offices[:30],
        elections=elections[:30],
        facilities=facilities,
        jurisdiction_stack=jurisdiction_stack,
        officials=officials_panel[:50],
        legislation=legislation_panel,
        finance_lobbying=[],
        budgets_debt_defense=[],
        media_gallery=media_gallery,
        meta=meta,
    )


def _patch_cached_payload_bounds(
    payload: dict[str, Any],
    *,
    n: float,
    s: float,
    e: float,
    w: float,
    center_lat: float,
    center_lng: float,
    started: float,
) -> dict[str, Any]:
    patched = dict(payload)
    patched["bounds"] = {"north": n, "south": s, "east": e, "west": w}
    patched["center"] = {"lat": center_lat, "lng": center_lng}
    payload_meta = dict(patched.get("meta") or {})
    payload_meta["total_ms"] = int((perf_counter() - started) * 1000)
    payload_meta["within_budget"] = payload_meta.get("total_ms", 0) <= payload_meta.get("budget_ms", 1200)
    patched["meta"] = payload_meta
    return patched


async def _fetch_live_provider_intel(
    *,
    place: CivicViewportPlace | None,
    n: float,
    s: float,
    e: float,
    w: float,
    center_lat: float,
    center_lng: float,
) -> tuple[
    list[CivicRepresentative],
    list[CivicElectionEvent],
    list[CivicFacility],
    list[dict[str, Any]],
    list[CivicProviderStatus],
    dict[str, int],
]:
    address = ", ".join(
        [
            x
            for x in [
                place.city if place else None,
                place.county if place else None,
                place.state if place else None,
                place.country if place else None,
            ]
            if x
        ]
    )
    state_code = place.state[:2].upper() if place and place.state and len(place.state) >= 2 else None
    provider_precedence = ["google_civic", "civicengine", "us_vote_foundation", "legiscan", "data_gov"]
    provider_results = await asyncio.gather(
        fetch_google_civic(address=address or ""),
        fetch_data_gov_catalog(query=(place.state if place and place.state else place.country if place and place.country else "government")),
        fetch_legiscan(state_code=state_code),
        fetch_civicengine(lat=center_lat, lng=center_lng),
        fetch_us_vote_foundation(state_code=state_code),
        fetch_arcgis_hub_facilities(north=n, south=s, east=e, west=w, limit=180),
    )
    representative_records: list[dict[str, Any]] = []
    election_records: list[dict[str, Any]] = []
    legislation_records: list[dict[str, Any]] = []
    facility_records: list[dict[str, Any]] = []
    provider_status: list[CivicProviderStatus] = []
    provider_counts: dict[str, int] = {}
    for result in provider_results:
        provider_counts[result.provider] = len(result.records)
        provider_status.append(
            CivicProviderStatus(
                provider=result.provider,
                status=result.status,
                fetched_at=result.fetched_at,
                records=len(result.records),
                notes=result.notes,
            )
        )
        for row in result.records:
            enriched = dict(row)
            enriched["_provider"] = result.provider
            if str(enriched.get("entity_type") or "").lower() == "facility" or enriched.get("office") == "Facility":
                facility_records.append(enriched)
            elif enriched.get("office") == "Election Event":
                election_records.append(enriched)
            elif enriched.get("office") == "Legislative Artifact":
                legislation_records.append(enriched)
            else:
                representative_records.append(enriched)
    representatives = _dedupe_representatives(representative_records, place, provider_precedence)
    elections = _elections_from_records(election_records, place)
    provider_facilities = _facilities_from_provider_records(facility_records, default_source="provider")
    return representatives, elections, provider_facilities, legislation_records, provider_status, provider_counts


@router.get("/viewport-intel", response_model=CivicUnifiedViewportResponse)
async def get_civic_viewport_intel(
    north: float = Query(...),
    south: float = Query(...),
    east: float = Query(...),
    west: float = Query(...),
    zoom: float = Query(4),
    db: AsyncSession = Depends(get_db_session),
) -> CivicUnifiedViewportResponse:
    started = perf_counter()
    n = max(north, south)
    s = min(north, south)
    e = east
    w = west
    center_lat, center_lng = _center(n, s, e, w)
    lod = _lod_from_zoom(zoom)
    cache_key = _cache_key(n, s, e, w, zoom)

    cached = await _load_cached_payload(db, cache_key)
    if cached:
        payload = _patch_cached_payload_bounds(
            cached,
            n=n,
            s=s,
            e=e,
            w=w,
            center_lat=center_lat,
            center_lng=center_lng,
            started=started,
        )
        return CivicUnifiedViewportResponse.model_validate(payload)

    place = await _resolve_place_from_mindex(db, center_lat, center_lng)
    if not place and _allow_live_geocode():
        place = await _reverse_place(n, s, e, w)
    elif place and (place.lat is None or place.lng is None):
        place = place.model_copy(update={"lat": center_lat, "lng": center_lng})

    jurisdiction_keys = _jurisdiction_keys_hierarchy(place)
    jurisdiction_key = _canonical_jurisdiction_key(place)

    for jkey in reversed(jurisdiction_keys):
        jcached = await _load_jurisdiction_cache_by_key(db, jkey, lod)
        if jcached:
            payload = _patch_cached_payload_bounds(
                jcached,
                n=n,
                s=s,
                e=e,
                w=w,
                center_lat=center_lat,
                center_lng=center_lng,
                started=started,
            )
            await _store_cached_payload(
                db=db,
                key=cache_key,
                north=n,
                south=s,
                east=e,
                west=w,
                zoom=zoom,
                lod=lod,
                place_name=place.display_name if place else None,
                jurisdiction_key=jkey,
                payload=payload,
            )
            await db.commit()
            return CivicUnifiedViewportResponse.model_validate(payload)

    representatives = await _load_officials_from_canonical_db(db, jurisdiction_keys)
    elections = await _load_elections_from_canonical_db(db, jurisdiction_keys, place)
    civic_facilities = await _load_civic_facilities_from_canonical_db(db, n, s, e, w)
    infra_facilities = await _load_facilities_from_mindex(db, n, s, e, w)
    facility_by_key: dict[str, CivicFacility] = {}
    for facility in civic_facilities + infra_facilities:
        key = f"{facility.type.lower()}|{facility.name.lower()}|{round(facility.lat, 4)}|{round(facility.lng, 4)}"
        existing = facility_by_key.get(key)
        if not existing:
            facility_by_key[key] = facility
            continue
        if not existing.image_url and facility.image_url:
            existing.image_url = facility.image_url
        if not existing.agency and facility.agency:
            existing.agency = facility.agency
    facilities = list(facility_by_key.values())[:350]
    legislation_records: list[dict[str, Any]] = []

    if _has_sufficient_canonical_data(representatives, elections, facilities):
        response = _build_viewport_response_payload(
            place=place,
            n=n,
            s=s,
            e=e,
            w=w,
            zoom=zoom,
            lod=lod,
            center_lat=center_lat,
            center_lng=center_lng,
            representatives=representatives,
            elections=elections,
            facilities=facilities,
            legislation_records=legislation_records,
            provider_status=[],
            provider_counts={"mindex:civic": len(representatives) + len(elections) + len(facilities)},
            started=started,
            source_lineage_note="mindex:civic",
        )
        await _store_cached_payload(
            db=db,
            key=cache_key,
            north=n,
            south=s,
            east=e,
            west=w,
            zoom=zoom,
            lod=lod,
            place_name=place.display_name if place else None,
            jurisdiction_key=jurisdiction_key,
            payload=response.model_dump(mode="json"),
        )
        await db.commit()
        return response

    if not _allow_live_provider_refresh():
        response = _build_viewport_response_payload(
            place=place,
            n=n,
            s=s,
            e=e,
            w=w,
            zoom=zoom,
            lod=lod,
            center_lat=center_lat,
            center_lng=center_lng,
            representatives=representatives,
            elections=elections,
            facilities=facilities,
            legislation_records=legislation_records,
            provider_status=[
                CivicProviderStatus(
                    provider="mindex",
                    status="empty",
                    fetched_at=datetime.now(timezone.utc),
                    records=0,
                    notes="No canonical civic data yet — run civic_viewport_sync ETL",
                )
            ],
            provider_counts={},
            started=started,
            source_lineage_note="mindex:pending-etl",
        )
        await _store_cached_payload(
            db=db,
            key=cache_key,
            north=n,
            south=s,
            east=e,
            west=w,
            zoom=zoom,
            lod=lod,
            place_name=place.display_name if place else None,
            jurisdiction_key=jurisdiction_key,
            payload=response.model_dump(mode="json"),
        )
        await db.commit()
        return response

    live_reps, live_elections, provider_facilities, legislation_records, provider_status, provider_counts = (
        await _fetch_live_provider_intel(
            place=place,
            n=n,
            s=s,
            e=e,
            w=w,
            center_lat=center_lat,
            center_lng=center_lng,
        )
    )
    representatives = live_reps or representatives
    elections = live_elections or elections
    for facility in provider_facilities:
        key = f"{facility.type.lower()}|{facility.name.lower()}|{round(facility.lat, 4)}|{round(facility.lng, 4)}"
        if key not in facility_by_key:
            facility_by_key[key] = facility
    facilities = list(facility_by_key.values())[:350]

    response = _build_viewport_response_payload(
        place=place,
        n=n,
        s=s,
        e=e,
        w=w,
        zoom=zoom,
        lod=lod,
        center_lat=center_lat,
        center_lng=center_lng,
        representatives=representatives,
        elections=elections,
        facilities=facilities,
        legislation_records=legislation_records,
        provider_status=provider_status,
        provider_counts=provider_counts,
        started=started,
        source_lineage_note="provider",
    )
    await _persist_canonical_snapshot(db, place, representatives, elections, facilities, provider_status)
    await _store_cached_payload(
        db=db,
        key=cache_key,
        north=n,
        south=s,
        east=e,
        west=w,
        zoom=zoom,
        lod=lod,
        place_name=place.display_name if place else None,
        jurisdiction_key=jurisdiction_key,
        payload=response.model_dump(mode="json"),
    )
    await db.commit()
    return response


async def refresh_viewport_intel_for_bounds(
    db: AsyncSession,
    north: float,
    south: float,
    east: float,
    west: float,
    zoom: float,
) -> CivicUnifiedViewportResponse:
    """Batch ETL entrypoint — live provider fetch, persist canonical tables, warm cache."""
    started = perf_counter()
    n = max(north, south)
    s = min(north, south)
    e = east
    w = west
    center_lat, center_lng = _center(n, s, e, w)
    lod = _lod_from_zoom(zoom)
    cache_key = _cache_key(n, s, e, w, zoom)

    place = await _resolve_place_from_mindex(db, center_lat, center_lng)
    if not place:
        place = await _reverse_place(n, s, e, w)
    elif place.lat is None or place.lng is None:
        place = place.model_copy(update={"lat": center_lat, "lng": center_lng})

    live_reps, live_elections, provider_facilities, legislation_records, provider_status, provider_counts = (
        await _fetch_live_provider_intel(
            place=place,
            n=n,
            s=s,
            e=e,
            w=w,
            center_lat=center_lat,
            center_lng=center_lng,
        )
    )
    infra_facilities = await _load_facilities_from_mindex(db, n, s, e, w)
    facility_by_key: dict[str, CivicFacility] = {}
    for facility in provider_facilities + infra_facilities:
        key = f"{facility.type.lower()}|{facility.name.lower()}|{round(facility.lat, 4)}|{round(facility.lng, 4)}"
        if key not in facility_by_key:
            facility_by_key[key] = facility
    facilities = list(facility_by_key.values())[:350]

    response = _build_viewport_response_payload(
        place=place,
        n=n,
        s=s,
        e=e,
        w=w,
        zoom=zoom,
        lod=lod,
        center_lat=center_lat,
        center_lng=center_lng,
        representatives=live_reps,
        elections=live_elections,
        facilities=facilities,
        legislation_records=legislation_records,
        provider_status=provider_status,
        provider_counts=provider_counts,
        started=started,
        source_lineage_note="etl:provider",
    )
    jurisdiction_key = _canonical_jurisdiction_key(place)
    await _persist_canonical_snapshot(db, place, live_reps, live_elections, facilities, provider_status)
    await _store_cached_payload(
        db=db,
        key=cache_key,
        north=n,
        south=s,
        east=e,
        west=w,
        zoom=zoom,
        lod=lod,
        place_name=place.display_name if place else None,
        jurisdiction_key=jurisdiction_key,
        payload=response.model_dump(mode="json"),
    )
    await db.commit()
    return response


# Legacy endpoint body removed — live fetch extracted to _fetch_live_provider_intel above.
