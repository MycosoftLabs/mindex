"""
TheYeasts.org Species Scraper

Scrapes yeast species data from https://theyeasts.org/species-search
Expected: ~3,502 yeast species

Source: TheYeasts.org - A comprehensive database of yeast biodiversity
"""

from __future__ import annotations

import re
import time
from typing import Dict, Generator, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from ..config import settings

THEYEASTS_BASE_URL = "https://theyeasts.org"
THEYEASTS_SPECIES_URL = f"{THEYEASTS_BASE_URL}/species-search"


def map_yeast_record(record: dict) -> dict:
    """Map scraped yeast record to MINDEX taxon format."""
    return {
        "canonical_name": record.get("scientific_name"),
        "rank": "species",
        "common_name": record.get("common_name"),
        "description": record.get("description"),
        "source": "theyeasts",
        "metadata": {
            "theyeasts_id": record.get("id"),
            "genus": record.get("genus"),
            "family": record.get("family"),
            "order": record.get("order"),
            "class": record.get("class_name"),
            "phylum": record.get("phylum"),
            "type_strain": record.get("type_strain"),
            "original_url": record.get("url"),
            "synonyms": record.get("synonyms", []),
        },
        "traits": [
            {"trait_name": "fungi_type", "value_text": "yeast"},
        ],
    }


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
    reraise=True,
)
def _fetch_species_list(client: httpx.Client, page: int = 1) -> Tuple[List[dict], int]:
    """Fetch species list page from TheYeasts.org."""
    params = {
        "page": page,
        "per_page": 100,
    }
    
    response = client.get(
        THEYEASTS_SPECIES_URL,
        params=params,
        timeout=30.0,
        headers={
            "User-Agent": "MINDEX-ETL/1.0 (Mycosoft Fungal Database; contact@mycosoft.org)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    response.raise_for_status()
    
    # Parse HTML response
    soup = BeautifulSoup(response.text, "html.parser")
    
    species_list = []
    
    # Find species entries in the page
    # Adjust selectors based on actual site structure
    species_rows = soup.select("table.species-table tbody tr, .species-list .species-item, .species-entry")
    
    if not species_rows:
        # Try alternative selectors
        species_rows = soup.select("table tr[data-species], .result-item, li.species")
    
    for row in species_rows:
        try:
            # Extract species data from row
            name_elem = row.select_one("td.name, .species-name, a.species-link, td:first-child")
            if not name_elem:
                continue
                
            scientific_name = name_elem.get_text(strip=True)
            if not scientific_name or len(scientific_name) < 3:
                continue
            
            # Extract genus from name
            parts = scientific_name.split()
            genus = parts[0] if parts else None
            
            # Get link to species page
            link_elem = row.select_one("a[href*='species'], a[href*='taxon']")
            species_url = None
            species_id = None
            if link_elem and link_elem.get("href"):
                href = link_elem["href"]
                if not href.startswith("http"):
                    href = f"{THEYEASTS_BASE_URL}{href}"
                species_url = href
                # Extract ID from URL
                id_match = re.search(r"/species/(\d+)|/taxon/(\d+)|id=(\d+)", href)
                if id_match:
                    species_id = id_match.group(1) or id_match.group(2) or id_match.group(3)
            
            # Get additional columns if available
            columns = row.select("td")
            family = columns[1].get_text(strip=True) if len(columns) > 1 else None
            order = columns[2].get_text(strip=True) if len(columns) > 2 else None
            
            species_data = {
                "id": species_id or scientific_name.replace(" ", "_").lower(),
                "scientific_name": scientific_name,
                "genus": genus,
                "family": family,
                "order": order,
                "url": species_url,
                "common_name": None,
                "description": None,
                "synonyms": [],
            }
            
            species_list.append(species_data)
            
        except Exception as e:
            print(f"Error parsing species row: {e}")
            continue
    
    # Get total pages
    pagination = soup.select_one(".pagination, .pager, nav[aria-label='pagination']")
    total_pages = 1
    if pagination:
        last_page = pagination.select_one("a:last-child, .last-page, [aria-label='Last']")
        if last_page:
            try:
                total_pages = int(re.search(r"\d+", last_page.get_text() or last_page.get("href", "")).group())
            except (ValueError, AttributeError):
                pass
    
    return species_list, total_pages


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
    reraise=True,
)
def _fetch_species_detail(client: httpx.Client, url: str) -> dict:
    """Fetch detailed species information from species page."""
    response = client.get(
        url,
        timeout=30.0,
        headers={
            "User-Agent": "MINDEX-ETL/1.0 (Mycosoft Fungal Database; contact@mycosoft.org)",
        },
    )
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    details = {}
    
    # Extract description
    desc_elem = soup.select_one(".description, .species-description, #description, p.summary")
    if desc_elem:
        details["description"] = desc_elem.get_text(strip=True)
    
    # Extract taxonomy
    taxonomy_section = soup.select_one(".taxonomy, .classification, #taxonomy")
    if taxonomy_section:
        for item in taxonomy_section.select("li, tr, .taxonomy-item"):
            label_elem = item.select_one(".label, th, strong")
            value_elem = item.select_one(".value, td, span")
            if label_elem and value_elem:
                label = label_elem.get_text(strip=True).lower().replace(":", "")
                value = value_elem.get_text(strip=True)
                if "class" in label:
                    details["class_name"] = value
                elif "phylum" in label:
                    details["phylum"] = value
                elif "family" in label:
                    details["family"] = value
                elif "order" in label:
                    details["order"] = value
    
    # Extract type strain
    strain_elem = soup.select_one(".type-strain, [data-field='type_strain'], .strain")
    if strain_elem:
        details["type_strain"] = strain_elem.get_text(strip=True)
    
    # Extract synonyms
    synonyms = []
    synonyms_section = soup.select_one(".synonyms, #synonyms, .alternative-names")
    if synonyms_section:
        for syn in synonyms_section.select("li, .synonym"):
            synonyms.append(syn.get_text(strip=True))
    details["synonyms"] = synonyms
    
    return details


def iter_theyeasts_species(
    *,
    max_pages: Optional[int] = None,
    delay_seconds: float = 1.0,
    fetch_details: bool = False,
    client: Optional[httpx.Client] = None,
) -> Generator[Tuple[dict, str, str], None, None]:
    """
    Iterate over all yeast species from TheYeasts.org.
    
    Yields:
        Tuple of (mapped_taxon, source_name, external_id)
    """
    close_client = False
    if client is None:
        client = httpx.Client()
        close_client = True
    
    try:
        page = 1
        total_pages = None
        
        while True:
            print(f"Fetching TheYeasts.org page {page}...", flush=True)
            
            species_list, detected_pages = _fetch_species_list(client, page)
            
            if total_pages is None:
                total_pages = detected_pages
                print(f"Total pages detected: {total_pages}", flush=True)
            
            if not species_list:
                print(f"No species found on page {page}, stopping.", flush=True)
                break
            
            for species in species_list:
                # Optionally fetch detailed info
                if fetch_details and species.get("url"):
                    try:
                        time.sleep(delay_seconds / 2)  # Smaller delay for detail pages
                        details = _fetch_species_detail(client, species["url"])
                        species.update(details)
                    except Exception as e:
                        print(f"Error fetching details for {species['scientific_name']}: {e}")
                
                mapped = map_yeast_record(species)
                external_id = str(species.get("id", species["scientific_name"]))
                
                yield mapped, "theyeasts", external_id
            
            page += 1
            
            if max_pages and page > max_pages:
                print(f"Reached max pages limit ({max_pages})", flush=True)
                break
            
            if total_pages and page > total_pages:
                print(f"Reached last page ({total_pages})", flush=True)
                break
            
            time.sleep(delay_seconds)
            
    finally:
        if close_client:
            client.close()


# Alternative API-based approach if available
def iter_theyeasts_api(
    *,
    max_records: Optional[int] = None,
    delay_seconds: float = 0.5,
    client: Optional[httpx.Client] = None,
) -> Generator[Tuple[dict, str, str], None, None]:
    """
    Try to use TheYeasts.org API if available.
    Falls back to HTML scraping if API is not accessible.
    """
    close_client = False
    if client is None:
        client = httpx.Client()
        close_client = True
    
    try:
        # Try API endpoint first
        api_url = f"{THEYEASTS_BASE_URL}/api/species"
        
        try:
            response = client.get(
                api_url,
                timeout=10.0,
                headers={"Accept": "application/json"},
            )
            
            if response.status_code == 200:
                data = response.json()
                species_list = data.get("species", data.get("results", data))
                
                if isinstance(species_list, list):
                    print(f"Using TheYeasts API, found {len(species_list)} species", flush=True)
                    
                    for i, species in enumerate(species_list):
                        if max_records and i >= max_records:
                            break
                        
                        record = {
                            "id": species.get("id"),
                            "scientific_name": species.get("name") or species.get("scientific_name"),
                            "genus": species.get("genus"),
                            "family": species.get("family"),
                            "order": species.get("order"),
                            "class_name": species.get("class"),
                            "phylum": species.get("phylum"),
                            "description": species.get("description"),
                            "type_strain": species.get("type_strain"),
                            "synonyms": species.get("synonyms", []),
                        }
                        
                        mapped = map_yeast_record(record)
                        external_id = str(record.get("id", record["scientific_name"]))
                        
                        yield mapped, "theyeasts", external_id
                    
                    return
                    
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError):
            pass
        
        # Fall back to HTML scraping
        print("API not available, falling back to HTML scraping...", flush=True)
        yield from iter_theyeasts_species(
            max_pages=None if not max_records else (max_records // 100 + 1),
            delay_seconds=delay_seconds,
            client=client,
        )
        
    finally:
        if close_client:
            client.close()
