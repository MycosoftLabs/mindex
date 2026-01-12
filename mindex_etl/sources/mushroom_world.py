"""
Mushroom.World Species Scraper

Scrapes mushroom species data from http://mushroom.world/mushrooms/namelist
Expected: 1,000+ mushroom species with descriptions and images

Source: Mushroom.World - Comprehensive mushroom identification database
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

MUSHROOM_WORLD_BASE_URL = "http://mushroom.world"
MUSHROOM_WORLD_NAMELIST_URL = f"{MUSHROOM_WORLD_BASE_URL}/mushrooms/namelist"


def map_mushroom_record(record: dict) -> dict:
    """Map scraped mushroom record to MINDEX taxon format."""
    traits = record.get("traits") or {}
    
    trait_list = [
        {"trait_name": "fungi_type", "value_text": "mushroom"},
    ]
    
    # Add traits from scraped data
    if traits.get("edibility"):
        trait_list.append({"trait_name": "edibility", "value_text": traits["edibility"]})
    if traits.get("habitat"):
        trait_list.append({"trait_name": "habitat", "value_text": traits["habitat"]})
    if traits.get("spore_print"):
        trait_list.append({"trait_name": "spore_print", "value_text": traits["spore_print"]})
    if traits.get("cap_shape"):
        trait_list.append({"trait_name": "cap_shape", "value_text": traits["cap_shape"]})
    if traits.get("gill_type"):
        trait_list.append({"trait_name": "gill_type", "value_text": traits["gill_type"]})
    
    return {
        "canonical_name": record.get("scientific_name") or record.get("name"),
        "rank": record.get("rank") or "species",
        "common_name": record.get("common_name"),
        "description": record.get("description"),
        "source": "mushroom_world",
        "metadata": {
            "mushroom_world_id": record.get("id"),
            "genus": record.get("genus"),
            "family": record.get("family"),
            "order": record.get("order"),
            "original_url": record.get("url"),
            "image_url": record.get("image_url"),
            "edibility": traits.get("edibility"),
            "habitat": traits.get("habitat"),
            "season": traits.get("season"),
            "distribution": traits.get("distribution"),
        },
        "traits": trait_list,
    }


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
    reraise=True,
)
def _fetch_namelist_page(client: httpx.Client, letter: str = None, page: int = 1) -> Tuple[List[dict], bool]:
    """
    Fetch species from the Mushroom.World namelist.
    
    Args:
        client: HTTP client
        letter: Filter by first letter (a-z) or None for all
        page: Page number
    
    Returns:
        Tuple of (species_list, has_more_pages)
    """
    url = MUSHROOM_WORLD_NAMELIST_URL
    params = {}
    
    if letter:
        params["letter"] = letter
    if page > 1:
        params["page"] = page
    
    response = client.get(
        url,
        params=params,
        timeout=60.0,
        headers={
            "User-Agent": "MINDEX-ETL/1.0 (Mycosoft Fungal Database; contact@mycosoft.org)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    species_list = []
    
    # Try multiple selector patterns for the species list
    selectors = [
        "table.mushroom-list tr",
        ".mushroom-list .mushroom-item",
        ".species-list li",
        "ul.namelist li",
        ".mushroom-name",
        "#content table tr",
        ".name-list a",
    ]
    
    species_elements = []
    for selector in selectors:
        species_elements = soup.select(selector)
        if species_elements:
            break
    
    # Also try to find all links that look like mushroom pages
    if not species_elements:
        species_elements = soup.select("a[href*='mushroom'], a[href*='species']")
    
    print(f"Found {len(species_elements)} elements for letter={letter} page={page}", flush=True)
    
    for elem in species_elements:
        try:
            scientific_name = None
            common_name = None
            url = None
            species_id = None
            
            if elem.name == "tr":
                # Table row format
                cells = elem.select("td")
                if not cells:
                    continue
                
                # Scientific name usually in first cell
                name_cell = cells[0]
                sci_name_elem = name_cell.select_one("i, em, .scientific-name, a")
                if sci_name_elem:
                    scientific_name = sci_name_elem.get_text(strip=True)
                else:
                    scientific_name = name_cell.get_text(strip=True)
                
                # Common name in second cell
                if len(cells) > 1:
                    common_name = cells[1].get_text(strip=True)
                
                # Get link
                link = name_cell.select_one("a")
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"{MUSHROOM_WORLD_BASE_URL}{href}"
                    url = href
                    
            elif elem.name == "li":
                # List item format
                sci_name_elem = elem.select_one("i, em, .scientific-name")
                if sci_name_elem:
                    scientific_name = sci_name_elem.get_text(strip=True)
                else:
                    # Try to extract from link
                    link = elem.select_one("a")
                    if link:
                        scientific_name = link.get_text(strip=True)
                    else:
                        scientific_name = elem.get_text(strip=True)
                
                # Common name might be in parentheses or separate element
                common_elem = elem.select_one(".common-name, small, span")
                if common_elem:
                    common_name = common_elem.get_text(strip=True)
                else:
                    # Try to extract from text in parentheses
                    full_text = elem.get_text()
                    paren_match = re.search(r"\(([^)]+)\)", full_text)
                    if paren_match:
                        common_name = paren_match.group(1)
                
                link = elem.select_one("a")
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"{MUSHROOM_WORLD_BASE_URL}{href}"
                    url = href
                    
            elif elem.name == "a":
                scientific_name = elem.get_text(strip=True)
                href = elem.get("href")
                if href and not href.startswith("http"):
                    href = f"{MUSHROOM_WORLD_BASE_URL}{href}"
                url = href
                
            else:
                # Other elements
                scientific_name = elem.get_text(strip=True)
                link = elem.find_parent("a") or elem.select_one("a")
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"{MUSHROOM_WORLD_BASE_URL}{href}"
                    url = href
            
            # Validate and clean name
            if not scientific_name or len(scientific_name) < 3:
                continue
            
            # Skip navigation elements
            if scientific_name.lower() in ["home", "search", "about", "contact", "next", "previous"]:
                continue
            
            # Extract genus
            parts = scientific_name.split()
            genus = parts[0] if parts else None
            
            # Generate ID
            if url:
                id_match = re.search(r"/mushroom/([^/]+)|/species/([^/]+)|id=(\w+)", url)
                if id_match:
                    species_id = id_match.group(1) or id_match.group(2) or id_match.group(3)
            if not species_id:
                species_id = scientific_name.replace(" ", "_").lower()
            
            species_data = {
                "id": species_id,
                "scientific_name": scientific_name,
                "common_name": common_name if common_name and common_name != scientific_name else None,
                "genus": genus,
                "url": url,
                "description": None,
                "image_url": None,
                "traits": {},
            }
            
            species_list.append(species_data)
            
        except Exception as e:
            print(f"Error parsing mushroom element: {e}")
            continue
    
    # Check for pagination
    has_more = False
    pagination = soup.select_one(".pagination, .pager, nav[aria-label='pagination']")
    if pagination:
        next_link = pagination.select_one("a.next, a[rel='next'], .next-page")
        has_more = next_link is not None
    
    return species_list, has_more


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
    traits = {}
    
    # Extract description
    desc_selectors = [
        ".description", "#description", ".mushroom-description",
        ".species-info p", ".content p:first-of-type"
    ]
    for selector in desc_selectors:
        desc_elem = soup.select_one(selector)
        if desc_elem:
            details["description"] = desc_elem.get_text(strip=True)
            break
    
    # Extract image
    img_selectors = [
        ".mushroom-image img", ".species-image img",
        ".main-image img", "#gallery img:first-of-type", "img.mushroom"
    ]
    for selector in img_selectors:
        img_elem = soup.select_one(selector)
        if img_elem and img_elem.get("src"):
            src = img_elem["src"]
            if not src.startswith("http"):
                src = f"{MUSHROOM_WORLD_BASE_URL}{src}"
            details["image_url"] = src
            break
    
    # Extract traits from info table
    info_table = soup.select_one(".info-table, .properties, .characteristics, table.traits")
    if info_table:
        for row in info_table.select("tr"):
            cells = row.select("td, th")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower().replace(" ", "_")
                value = cells[1].get_text(strip=True)
                
                if "edib" in key or "poison" in key:
                    traits["edibility"] = value
                elif "habitat" in key or "where" in key:
                    traits["habitat"] = value
                elif "spore" in key:
                    traits["spore_print"] = value
                elif "cap" in key and "shape" in key:
                    traits["cap_shape"] = value
                elif "gill" in key:
                    traits["gill_type"] = value
                elif "season" in key or "when" in key:
                    traits["season"] = value
                elif "distribution" in key or "region" in key:
                    traits["distribution"] = value
    
    # Also look for labeled spans/divs
    for label_elem in soup.select("[data-label], .trait-label, dt"):
        label = label_elem.get_text(strip=True).lower()
        value_elem = label_elem.find_next_sibling() or label_elem.select_one("+ dd, + span")
        if value_elem:
            value = value_elem.get_text(strip=True)
            if "edib" in label:
                traits["edibility"] = value
            elif "habitat" in label:
                traits["habitat"] = value
    
    # Extract family/order from taxonomy section
    taxonomy = soup.select_one(".taxonomy, .classification, #taxonomy")
    if taxonomy:
        for item in taxonomy.select("li, span, a"):
            text = item.get_text(strip=True)
            if "aceae" in text.lower():  # Family names end in -aceae
                details["family"] = text
            elif "ales" in text.lower():  # Order names end in -ales
                details["order"] = text
    
    details["traits"] = traits
    return details


def iter_mushroom_world_species(
    *,
    letters: Optional[List[str]] = None,
    max_pages_per_letter: int = 100,
    max_pages: Optional[int] = None,  # Alias for max_pages_per_letter (scheduler compatibility)
    delay_seconds: float = 1.0,
    fetch_details: bool = False,
    client: Optional[httpx.Client] = None,
    **kwargs,  # Absorb any extra parameters
) -> Generator[Tuple[dict, str, str], None, None]:
    """
    Iterate over all mushroom species from Mushroom.World.
    
    Args:
        letters: List of letters to scrape (a-z), or None for all
        max_pages_per_letter: Maximum pages to fetch per letter
        delay_seconds: Delay between requests
        fetch_details: Whether to fetch detailed species pages
        client: Optional HTTP client
    
    Yields:
        Tuple of (mapped_taxon, source_name, external_id)
    """
    close_client = False
    if client is None:
        client = httpx.Client()
        close_client = True
    
    # Use max_pages as alias for max_pages_per_letter if provided
    if max_pages is not None:
        max_pages_per_letter = max_pages
    
    if letters is None:
        letters = list("abcdefghijklmnopqrstuvwxyz")
    
    seen_names = set()
    total_count = 0
    
    try:
        # First try to get all species without letter filter
        print("Attempting to fetch complete species list...", flush=True)
        
        page = 1
        while page <= max_pages_per_letter:
            species_list, has_more = _fetch_namelist_page(client, letter=None, page=page)
            
            if not species_list:
                break
            
            for species in species_list:
                name = species.get("scientific_name", "").lower()
                if name in seen_names:
                    continue
                seen_names.add(name)
                
                # Optionally fetch details
                if fetch_details and species.get("url"):
                    try:
                        time.sleep(delay_seconds / 2)
                        details = _fetch_species_detail(client, species["url"])
                        species.update(details)
                    except Exception as e:
                        print(f"Error fetching details for {species['scientific_name']}: {e}")
                
                mapped = map_mushroom_record(species)
                external_id = str(species.get("id", species["scientific_name"]))
                
                yield mapped, "mushroom_world", external_id
                total_count += 1
            
            if not has_more:
                break
            
            page += 1
            time.sleep(delay_seconds)
        
        # If we got species, we're done
        if total_count > 100:
            print(f"Fetched {total_count} species from main list", flush=True)
            return
        
        # Otherwise, try letter-by-letter approach
        print("Trying letter-by-letter approach...", flush=True)
        
        for letter in letters:
            print(f"Fetching species starting with '{letter.upper()}'...", flush=True)
            
            page = 1
            while page <= max_pages_per_letter:
                species_list, has_more = _fetch_namelist_page(client, letter=letter, page=page)
                
                if not species_list:
                    break
                
                for species in species_list:
                    name = species.get("scientific_name", "").lower()
                    if name in seen_names:
                        continue
                    seen_names.add(name)
                    
                    if fetch_details and species.get("url"):
                        try:
                            time.sleep(delay_seconds / 2)
                            details = _fetch_species_detail(client, species["url"])
                            species.update(details)
                        except Exception as e:
                            print(f"Error fetching details: {e}")
                    
                    mapped = map_mushroom_record(species)
                    external_id = str(species.get("id", species["scientific_name"]))
                    
                    yield mapped, "mushroom_world", external_id
                    total_count += 1
                
                if not has_more:
                    break
                
                page += 1
                time.sleep(delay_seconds)
            
            time.sleep(delay_seconds)
        
        print(f"Total species fetched from Mushroom.World: {total_count}", flush=True)
        
    finally:
        if close_client:
            client.close()


# Legacy API-based function (keep for backwards compatibility)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def _fetch_species_api(client: httpx.Client, page: int, page_size: int) -> dict:
    """Try API endpoint if available."""
    resp = client.get(
        f"{settings.mushroom_world_base_url}/api/species",
        params={"page": page, "pageSize": page_size},
        timeout=settings.http_timeout,
    )
    resp.raise_for_status()
    return resp.json()


def iter_mushroom_world_api(
    *,
    page_size: int = 100,
    max_pages: Optional[int] = None,
    client: Optional[httpx.Client] = None,
) -> Generator[Dict, None, None]:
    """
    Original API-based iterator (legacy compatibility).
    Falls back to HTML scraping if API unavailable.
    """
    own_client = False
    if client is None:
        client = httpx.Client()
        own_client = True
    try:
        # Try API first
        try:
            page = 1
            while True:
                payload = _fetch_species_api(client, page, page_size)
                results = payload.get("results", [])
                if not results:
                    break
                for record in results:
                    traits = record.get("traits") or {}
                    yield {
                        "canonical_name": record.get("name"),
                        "rank": record.get("rank") or "species",
                        "common_name": record.get("commonName"),
                        "description": record.get("description"),
                        "source": "mushroom_world",
                        "metadata": record,
                        "traits": [
                            {"trait_name": key, "value_text": value}
                            for key, value in traits.items()
                            if isinstance(value, str)
                        ],
                    }
                page += 1
                if max_pages and page > max_pages:
                    break
            return
        except (httpx.HTTPStatusError, httpx.RequestError):
            pass
        
        # Fall back to HTML scraping
        print("API unavailable, using HTML scraper...", flush=True)
        for mapped, source, ext_id in iter_mushroom_world_species(client=client):
            yield mapped
            
    finally:
        if own_client:
            client.close()
