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
from typing import Dict, Generator, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from ..config import settings

logger = logging.getLogger(__name__)

# Alternative endpoints to try
FUNGIDB_ENDPOINTS = [
    "https://fungidb.org/fungidb/service/record-types/organism/searches/GenesByTaxonGene/reports/standard",
    "https://fungidb.org/fungidb/service/record-types/dataset/searches/AllDatasets/reports/standard",
    "https://veupathdb.org/veupathdb/service/record-types/organism/searches/OrganismsByText/reports/standard",
]


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
