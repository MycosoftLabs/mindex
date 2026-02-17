"""
FungiDB Genome Data Fetcher

FungiDB is a genomic and functional genomic database for the kingdom Fungi.
This module fetches genome metadata for fungal species.

Note: The FungiDB API endpoint may change. This module tries multiple approaches:
1. Direct API endpoint
2. VEuPathDB web services (FungiDB is part of VEuPathDB)
3. Returns empty results gracefully if API is unavailable
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Generator, Optional, List

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from ..config import settings

logger = logging.getLogger(__name__)

# Alternative endpoints to try
FUNGIDB_ENDPOINTS = [
    "https://fungidb.org/fungidb/service/record-types/organism/searches/GenesByTaxonGene/reports/standard",
    "https://fungidb.org/fungidb/service/record-types/dataset/searches/AllDatasets/reports/standard",
    "https://veupathdb.org/veupathdb/service/record-types/organism/searches/OrganismsByText/reports/standard",
]

FUNGIDB_DOWNLOADS_INDEX = "https://fungidb.org/common/downloads/Current_Release/"


def _parse_directory_listing(html: str) -> List[str]:
    """
    Parse a simple Apache-style directory index into a list of subdirectory names.
    We use this as a robust fallback when the JSON API endpoints change.
    """
    soup = BeautifulSoup(html, "html.parser")
    dirs: List[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        if not href.endswith("/"):
            continue
        if href in ("../", "/"):
            continue
        # Ignore obvious non-organism folders
        if href.lower().startswith(("index", "readme")):
            continue
        # Directory names are usually safe ASCII; keep only simple tokens
        name = href.strip("/").strip()
        if not name:
            continue
        if not re.match(r"^[A-Za-z0-9_.-]+$", name):
            continue
        dirs.append(name)
    # De-dupe while preserving order
    seen = set()
    out: List[str] = []
    for d in dirs:
        if d in seen:
            continue
        seen.add(d)
        out.append(d)
    return out


def _iter_download_release_dirs(client: httpx.Client, limit: int = 500) -> Generator[Dict, None, None]:
    """
    FungiDB provides public genome downloads under Current_Release/.
    This yields lightweight genome metadata records based on the release directory listing.
    """
    resp = client.get(
        FUNGIDB_DOWNLOADS_INDEX,
        timeout=60,
        headers={"User-Agent": "MINDEX-ETL/1.0 (Mycosoft; contact@mycosoft.org)"},
    )
    resp.raise_for_status()
    dirs = _parse_directory_listing(resp.text)
    if not dirs:
        return
    for d in dirs[:limit]:
        base_url = f"{FUNGIDB_DOWNLOADS_INDEX}{d}/"
        yield {
            "taxon_name": d,  # best-effort; can be canonicalized later
            "accession": d,
            "assembly_level": None,
            "release_date": None,
            "source": "fungidb",
            "metadata": {
                "download_base_url": base_url,
                "source_api": "downloads_index",
            },
        }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(2),
    retry=retry_if_exception_type((httpx.RequestError,)),
    reraise=True,
)
def _fetch_page(client: httpx.Client, page: int, page_size: int) -> dict:
    """Try to fetch from FungiDB API."""
    resp = client.get(
        f"{settings.fungidb_base_url}/genomes",
        params={"page": page, "pageSize": page_size, "kingdom": "Fungi"},
        timeout=settings.http_timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _try_veupathdb_organisms(client: httpx.Client, limit: int = 100) -> list:
    """Try VEuPathDB organisms endpoint as fallback."""
    try:
        # VEuPathDB provides a unified API for all pathogen DBs including FungiDB
        resp = client.post(
            "https://fungidb.org/fungidb/service/record-types/organism/searches/AllOrganisms/reports/standard",
            json={
                "searchConfig": {
                    "parameters": {},
                },
                "reportConfig": {
                    "pagination": {"offset": 0, "numRecords": limit},
                    "attributes": ["primary_key", "organism_name", "strain", "ncbi_tax_id"],
                },
            },
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("records", [])
    except Exception as e:
        logger.warning(f"VEuPathDB fallback failed: {e}")
        return []


def iter_fungidb_genomes(
    *,
    page_size: int = 100,
    max_pages: Optional[int] = None,
    client: Optional[httpx.Client] = None,
    **kwargs,  # Accept extra params for scheduler compatibility
) -> Generator[Dict, None, None]:
    """
    Iterate over FungiDB genome records.
    
    Tries the main API first, then falls back to VEuPathDB API.
    Returns empty iterator gracefully if all APIs are unavailable.
    """
    own_client = False
    if client is None:
        client = httpx.Client()
        own_client = True
    
    try:
        # Try main API first
        try:
            page = 1
            while True:
                payload = _fetch_page(client, page, page_size)
                results = payload.get("results", [])
                if not results:
                    break
                for record in results:
                    yield {
                        "taxon_name": record.get("organismName"),
                        "accession": record.get("accession"),
                        "assembly_level": record.get("assemblyLevel"),
                        "release_date": record.get("releaseDate"),
                        "source": "fungidb",
                        "metadata": record,
                    }
                page += 1
                if max_pages and page > max_pages:
                    break
            return  # Success with main API
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("FungiDB main API returned 404, trying fallback...")
            else:
                raise
        
        # Try VEuPathDB fallback
        logger.info("Trying VEuPathDB organisms endpoint...")
        organisms = _try_veupathdb_organisms(client, limit=page_size * (max_pages or 10))
        
        if organisms:
            for record in organisms:
                attrs = record.get("attributes", {})
                yield {
                    "taxon_name": attrs.get("organism_name", "Unknown"),
                    "accession": attrs.get("primary_key"),
                    "assembly_level": None,
                    "release_date": None,
                    "source": "fungidb",
                    "metadata": {
                        "ncbi_tax_id": attrs.get("ncbi_tax_id"),
                        "strain": attrs.get("strain"),
                        "source_api": "veupathdb",
                    },
                }
            return

        # Final fallback: scrape the public Current_Release downloads index
        logger.info("Trying public downloads index fallback...")
        fallback_limit = page_size * (max_pages or 5)
        for record in _iter_download_release_dirs(client, limit=fallback_limit):
            yield record
        return
        
        # All APIs failed, return empty
        logger.warning("FungiDB: All API endpoints unavailable. Skipping this source.")
        return
        
    except Exception as e:
        logger.error(f"FungiDB sync error: {e}")
        # Return gracefully instead of raising
        return
        
    finally:
        if own_client:
            client.close()
