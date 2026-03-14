"""
GBIF (Global Biodiversity Information Facility) Data Source
============================================================
Fetch occurrence and species data from GBIF API.
https://www.gbif.org/developer/summary

Supports configurable domain selectors: all-life, fungi-only, or future per-kingdom mode.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

GBIF_API = "https://api.gbif.org/v1"
FUNGI_KINGDOM_KEY = 5  # GBIF key for Kingdom Fungi (used when domain_mode="fungi")


def _species_root_params(domain_mode: str) -> Dict[str, int]:
    """Return root filter params for species search based on domain_mode."""
    mode = (domain_mode or getattr(settings, "gbif_domain_mode", "fungi")).strip().lower()
    if mode == "all":
        return {}
    if mode == "fungi":
        return {"highertaxonKey": FUNGI_KINGDOM_KEY}
    # Future: "5,4" for fungi+plants -> would need different API usage
    return {"highertaxonKey": FUNGI_KINGDOM_KEY}


def _occurrence_root_params(domain_mode: str) -> Dict[str, int]:
    """Return root filter params for occurrence search based on domain_mode."""
    mode = (domain_mode or getattr(settings, "gbif_domain_mode", "fungi")).strip().lower()
    if mode == "all":
        return {}
    if mode == "fungi":
        return {"kingdomKey": FUNGI_KINGDOM_KEY}
    return {"kingdomKey": FUNGI_KINGDOM_KEY}


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def _fetch_species_page(
    client: httpx.Client,
    offset: int,
    limit: int,
    domain_mode: str = "fungi",
) -> dict:
    """Fetch species from GBIF Species API with configurable root filter."""
    params: Dict[str, Any] = {
        "rank": "SPECIES",
        "status": "ACCEPTED",
        "offset": offset,
        "limit": limit,
    }
    params.update(_species_root_params(domain_mode))
    resp = client.get(
        f"{GBIF_API}/species/search",
        params=params,
        timeout=60,  # Longer timeout for GBIF
        headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft Biodiversity Database; contact@mycosoft.org)"},
    )
    resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_occurrences_page(
    client: httpx.Client,
    offset: int,
    limit: int,
    domain_mode: str = "fungi",
    taxon_key: Optional[int] = None,
) -> dict:
    """Fetch occurrence records from GBIF Occurrence API with configurable root filter."""
    params: Dict[str, Any] = {
        "hasCoordinate": "true",
        "hasGeospatialIssue": "false",
        "offset": offset,
        "limit": limit,
    }
    params.update(_occurrence_root_params(domain_mode))
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
    domain_mode: Optional[str] = None,
) -> Generator[Dict, None, None]:
    """Iterate through GBIF species with configurable domain (all-life or fungi-only)."""
    mode = domain_mode or getattr(settings, "gbif_domain_mode", "fungi")
    with httpx.Client() as client:
        offset = 0
        page = 1
        while True:
            payload = _fetch_species_page(client, offset, limit, domain_mode=mode)
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
    domain_mode: Optional[str] = None,
    taxon_key: Optional[int] = None,
) -> Generator[Dict, None, None]:
    """Iterate through GBIF occurrence records with configurable domain (all-life or fungi-only)."""
    mode = domain_mode or getattr(settings, "gbif_domain_mode", "fungi")
    with httpx.Client() as client:
        offset = 0
        page = 1
        while True:
            payload = _fetch_occurrences_page(
                client, offset, limit, domain_mode=mode, taxon_key=taxon_key
            )
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
