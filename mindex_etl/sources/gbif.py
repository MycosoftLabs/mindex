"""
GBIF (Global Biodiversity Information Facility) Data Source
============================================================
Fetch fungal occurrence and species data from GBIF API.
https://www.gbif.org/developer/summary
"""
from __future__ import annotations

import time
from typing import Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

GBIF_API = "https://api.gbif.org/v1"
FUNGI_KINGDOM_KEY = 5  # GBIF key for Kingdom Fungi


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def _fetch_species_page(
    client: httpx.Client,
    offset: int,
    limit: int,
) -> dict:
    """Fetch species from GBIF Species API."""
    resp = client.get(
        f"{GBIF_API}/species/search",
        params={
            "highertaxonKey": FUNGI_KINGDOM_KEY,
            "rank": "SPECIES",
            "status": "ACCEPTED",
            "offset": offset,
            "limit": limit,
        },
        timeout=60,  # Longer timeout for GBIF
        headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft Fungal Database; contact@mycosoft.org)"},
    )
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_occurrences_page(
    client: httpx.Client,
    offset: int,
    limit: int,
    taxon_key: Optional[int] = None,
) -> dict:
    """Fetch occurrence records from GBIF Occurrence API."""
    params = {
        "kingdomKey": FUNGI_KINGDOM_KEY,
        "hasCoordinate": "true",
        "hasGeospatialIssue": "false",
        "offset": offset,
        "limit": limit,
    }
    if taxon_key:
        params["taxonKey"] = taxon_key

    resp = client.get(
        f"{GBIF_API}/occurrence/search",
        params=params,
        timeout=settings.http_timeout,
        headers={"User-Agent": "mindex-etl/0.1"},
    )
    resp.raise_for_status()
    return resp.json()


def map_gbif_species(record: dict) -> dict:
    """Map GBIF species to MINDEX taxon format."""
    return {
        "canonical_name": record.get("canonicalName") or record.get("scientificName"),
        "rank": (record.get("rank") or "SPECIES").lower(),
        "common_name": record.get("vernacularName"),
        "authority": record.get("authorship"),
        "description": None,  # GBIF doesn't provide descriptions in basic search
        "source": "gbif",
        "metadata": {
            "gbif_key": record.get("key"),
            "gbif_species_key": record.get("speciesKey"),
            "kingdom": record.get("kingdom"),
            "phylum": record.get("phylum"),
            "class": record.get("class"),
            "order": record.get("order"),
            "family": record.get("family"),
            "genus": record.get("genus"),
            "num_occurrences": record.get("numOccurrences"),
        },
    }


def map_gbif_occurrence(record: dict) -> dict:
    """Map GBIF occurrence to MINDEX observation format."""
    # Extract media
    media = []
    for m in record.get("media", []):
        if m.get("type") == "StillImage":
            media.append({
                "url": m.get("identifier"),
                "format": m.get("format"),
                "license": m.get("license"),
                "creator": m.get("creator"),
            })

    return {
        "source": "gbif",
        "source_id": str(record.get("key")),
        "observed_at": record.get("eventDate"),
        "observer": record.get("recordedBy"),
        "lat": record.get("decimalLatitude"),
        "lng": record.get("decimalLongitude"),
        "accuracy_m": record.get("coordinateUncertaintyInMeters"),
        "taxon_name": record.get("species") or record.get("scientificName"),
        "taxon_rank": (record.get("taxonRank") or "species").lower(),
        "taxon_gbif_key": record.get("speciesKey") or record.get("taxonKey"),
        "photos": media,
        "notes": record.get("occurrenceRemarks"),
        "metadata": {
            "gbif_key": record.get("key"),
            "dataset_key": record.get("datasetKey"),
            "institution_code": record.get("institutionCode"),
            "collection_code": record.get("collectionCode"),
            "catalog_number": record.get("catalogNumber"),
            "basis_of_record": record.get("basisOfRecord"),
            "country": record.get("country"),
            "country_code": record.get("countryCode"),
            "state_province": record.get("stateProvince"),
            "locality": record.get("locality"),
        },
    }


def iter_gbif_species(
    *,
    limit: int = 100,
    max_pages: Optional[int] = None,
    delay_seconds: float = 0.3,
) -> Generator[Dict, None, None]:
    """Iterate through GBIF fungal species."""
    with httpx.Client() as client:
        offset = 0
        page = 1
        while True:
            payload = _fetch_species_page(client, offset, limit)
            results = payload.get("results", [])

            if not results:
                break

            for record in results:
                if record.get("canonicalName"):
                    yield map_gbif_species(record)

            # Check if there are more pages
            if payload.get("endOfRecords", True):
                break

            offset += limit
            page += 1
            if max_pages and page > max_pages:
                break

            time.sleep(delay_seconds)


def iter_gbif_occurrences(
    *,
    limit: int = 100,
    max_pages: Optional[int] = None,
    delay_seconds: float = 0.3,
) -> Generator[Dict, None, None]:
    """Iterate through GBIF fungal occurrence records."""
    with httpx.Client() as client:
        offset = 0
        page = 1
        while True:
            payload = _fetch_occurrences_page(client, offset, limit)
            results = payload.get("results", [])

            if not results:
                break

            for record in results:
                yield map_gbif_occurrence(record)

            if payload.get("endOfRecords", True):
                break

            offset += limit
            page += 1
            if max_pages and page > max_pages:
                break

            time.sleep(delay_seconds)
