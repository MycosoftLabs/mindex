"""
Fusarium.org Species Scraper

Scrapes Fusarium species data from https://www.fusarium.org/page/SpeciesListFusariumAll
Expected: ~408 Fusarium species (excluding nom. inval., synonymous names)

Source: Fusarium.org - The Fusarium Species Database
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

FUSARIUM_BASE_URL = "https://www.fusarium.org"
FUSARIUM_SPECIES_LIST_URL = f"{FUSARIUM_BASE_URL}/page/SpeciesListFusariumAll"


def map_fusarium_record(record: dict) -> dict:
    """Map scraped Fusarium record to MINDEX taxon format."""
    return {
        "canonical_name": record.get("scientific_name"),
        "rank": "species",
        "common_name": record.get("common_name"),
        "description": record.get("description"),
        "authority": record.get("authority"),
        "source": "fusarium",
        "metadata": {
            "fusarium_id": record.get("id"),
            "genus": "Fusarium",
            "family": "Nectriaceae",
            "order": "Hypocreales",
            "class": "Sordariomycetes",
            "phylum": "Ascomycota",
            "kingdom": "Fungi",
            "type_strain": record.get("type_strain"),
            "original_url": record.get("url"),
            "status": record.get("status"),
            "basionym": record.get("basionym"),
            "synonyms": record.get("synonyms", []),
            "hosts": record.get("hosts", []),
            "geographic_distribution": record.get("distribution"),
        },
        "traits": [
            {"trait_name": "fungi_type", "value_text": "mold"},
            {"trait_name": "pathogenic", "value_text": "true"},
        ],
    }


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
    reraise=True,
)
def _fetch_species_list(client: httpx.Client) -> List[dict]:
    """Fetch the complete species list from Fusarium.org."""
    response = client.get(
        FUSARIUM_SPECIES_LIST_URL,
        timeout=60.0,
        headers={
            "User-Agent": "MINDEX-ETL/1.0 (Mycosoft Fungal Database; contact@mycosoft.org)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    species_list = []
    
    # Find species entries - Fusarium.org uses a table or list format
    # Try multiple selectors to find species
    species_elements = soup.select(
        "table.species-list tr, "
        ".species-list li, "
        "table tbody tr, "
        "#content table tr, "
        ".fusarium-species, "
        "ul.species li, "
        "ol.species li"
    )
    
    # If no table/list found, try to find species links directly
    if not species_elements:
        species_elements = soup.select("a[href*='species'], a[href*='fusarium'], .species-name")
    
    # Also try to find in paragraph format (some databases list species in text)
    if not species_elements:
        content = soup.select_one("#content, .content, main, article")
        if content:
            # Look for italic text (species names are usually italicized)
            species_elements = content.select("i, em, .species-name, .scientific-name")
    
    print(f"Found {len(species_elements)} potential species elements", flush=True)
    
    seen_names = set()
    
    for i, elem in enumerate(species_elements):
        try:
            # Extract species name
            scientific_name = None
            authority = None
            status = "accepted"
            url = None
            
            # Check if it's a table row
            if elem.name == "tr":
                cells = elem.select("td")
                if not cells:
                    continue
                
                # First cell usually contains species name
                name_cell = cells[0]
                name_elem = name_cell.select_one("i, em, a, .species-name")
                if name_elem:
                    scientific_name = name_elem.get_text(strip=True)
                else:
                    scientific_name = name_cell.get_text(strip=True)
                
                # Look for authority in second cell or after name
                if len(cells) > 1:
                    authority = cells[1].get_text(strip=True)
                
                # Look for status indicators
                row_text = elem.get_text().lower()
                if "nom. inval" in row_text or "invalid" in row_text:
                    status = "invalid"
                elif "synonym" in row_text:
                    status = "synonym"
                elif "unclear" in row_text:
                    status = "unclear"
                
                # Get link if available
                link = name_cell.select_one("a")
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"{FUSARIUM_BASE_URL}{href}"
                    url = href
                    
            elif elem.name == "li":
                # List item format
                name_elem = elem.select_one("i, em, a, .species-name")
                if name_elem:
                    scientific_name = name_elem.get_text(strip=True)
                else:
                    scientific_name = elem.get_text(strip=True)
                
                # Extract authority from remaining text
                full_text = elem.get_text(strip=True)
                if scientific_name and len(full_text) > len(scientific_name):
                    authority = full_text[len(scientific_name):].strip()
                    # Clean up authority
                    authority = re.sub(r"^[\s,\-–]+", "", authority)
                
                link = elem.select_one("a")
                if link and link.get("href"):
                    href = link["href"]
                    if not href.startswith("http"):
                        href = f"{FUSARIUM_BASE_URL}{href}"
                    url = href
                    
            elif elem.name == "a":
                scientific_name = elem.get_text(strip=True)
                href = elem.get("href")
                if href and not href.startswith("http"):
                    href = f"{FUSARIUM_BASE_URL}{href}"
                url = href
                
            else:
                # Other element types (i, em, span)
                scientific_name = elem.get_text(strip=True)
                parent_link = elem.find_parent("a")
                if parent_link and parent_link.get("href"):
                    href = parent_link["href"]
                    if not href.startswith("http"):
                        href = f"{FUSARIUM_BASE_URL}{href}"
                    url = href
            
            # Clean and validate name
            if not scientific_name:
                continue
                
            scientific_name = scientific_name.strip()
            
            # Skip if too short or doesn't look like a species name
            if len(scientific_name) < 5:
                continue
            
            # Skip if not a Fusarium species
            if not scientific_name.lower().startswith("fusarium"):
                # Check if it's just a species epithet
                if " " not in scientific_name:
                    scientific_name = f"Fusarium {scientific_name}"
                elif not any(c.isupper() for c in scientific_name):
                    continue
            
            # Skip duplicates
            if scientific_name.lower() in seen_names:
                continue
            seen_names.add(scientific_name.lower())
            
            # Skip invalid names based on status
            if status in ("invalid", "synonym"):
                continue
            
            # Generate ID from name
            species_id = scientific_name.replace(" ", "_").lower()
            if url:
                id_match = re.search(r"/species/([^/]+)|id=(\w+)", url)
                if id_match:
                    species_id = id_match.group(1) or id_match.group(2)
            
            species_data = {
                "id": species_id,
                "scientific_name": scientific_name,
                "authority": authority,
                "status": status,
                "url": url,
                "common_name": None,
                "description": None,
                "type_strain": None,
                "basionym": None,
                "synonyms": [],
                "hosts": [],
                "distribution": None,
            }
            
            species_list.append(species_data)
            
        except Exception as e:
            print(f"Error parsing element {i}: {e}")
            continue
    
    print(f"Extracted {len(species_list)} valid Fusarium species", flush=True)
    return species_list


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
    desc_selectors = [
        ".description", "#description", ".species-description",
        "p.summary", ".morphology", "#morphology"
    ]
    for selector in desc_selectors:
        desc_elem = soup.select_one(selector)
        if desc_elem:
            details["description"] = desc_elem.get_text(strip=True)
            break
    
    # Extract type strain
    type_selectors = [".type-strain", "#type-strain", "[data-field='type_strain']"]
    for selector in type_selectors:
        strain_elem = soup.select_one(selector)
        if strain_elem:
            details["type_strain"] = strain_elem.get_text(strip=True)
            break
    
    # Extract hosts
    hosts = []
    host_section = soup.select_one(".hosts, #hosts, .host-range")
    if host_section:
        for host in host_section.select("li, .host"):
            hosts.append(host.get_text(strip=True))
    details["hosts"] = hosts
    
    # Extract geographic distribution
    dist_section = soup.select_one(".distribution, #distribution, .geography")
    if dist_section:
        details["distribution"] = dist_section.get_text(strip=True)
    
    # Extract basionym
    basionym_elem = soup.select_one(".basionym, #basionym")
    if basionym_elem:
        details["basionym"] = basionym_elem.get_text(strip=True)
    
    # Extract synonyms
    synonyms = []
    syn_section = soup.select_one(".synonyms, #synonyms")
    if syn_section:
        for syn in syn_section.select("li, .synonym"):
            synonyms.append(syn.get_text(strip=True))
    details["synonyms"] = synonyms
    
    return details


def iter_fusarium_species(
    *,
    delay_seconds: float = 1.0,
    fetch_details: bool = False,
    client: Optional[httpx.Client] = None,
) -> Generator[Tuple[dict, str, str], None, None]:
    """
    Iterate over all Fusarium species from Fusarium.org.
    
    Yields:
        Tuple of (mapped_taxon, source_name, external_id)
    """
    close_client = False
    if client is None:
        client = httpx.Client()
        close_client = True
    
    try:
        print("Fetching Fusarium species list...", flush=True)
        species_list = _fetch_species_list(client)
        
        print(f"Processing {len(species_list)} Fusarium species...", flush=True)
        
        for i, species in enumerate(species_list):
            # Optionally fetch detailed info
            if fetch_details and species.get("url"):
                try:
                    time.sleep(delay_seconds)
                    details = _fetch_species_detail(client, species["url"])
                    species.update(details)
                except Exception as e:
                    print(f"Error fetching details for {species['scientific_name']}: {e}")
            
            mapped = map_fusarium_record(species)
            external_id = str(species.get("id", species["scientific_name"]))
            
            yield mapped, "fusarium", external_id
            
            if (i + 1) % 50 == 0:
                print(f"Processed {i + 1}/{len(species_list)} Fusarium species", flush=True)
        
        print(f"Completed processing {len(species_list)} Fusarium species", flush=True)
        
    finally:
        if close_client:
            client.close()


# Hardcoded fallback list for known Fusarium species
KNOWN_FUSARIUM_SPECIES = [
    "Fusarium oxysporum",
    "Fusarium graminearum",
    "Fusarium verticillioides",
    "Fusarium solani",
    "Fusarium proliferatum",
    "Fusarium fujikuroi",
    "Fusarium culmorum",
    "Fusarium avenaceum",
    "Fusarium equiseti",
    "Fusarium poae",
    "Fusarium sporotrichioides",
    "Fusarium tricinctum",
    "Fusarium sambucinum",
    "Fusarium semitectum",
    "Fusarium subglutinans",
    "Fusarium moniliforme",
    "Fusarium redolens",
    "Fusarium acuminatum",
    "Fusarium chlamydosporum",
    "Fusarium compactum",
    "Fusarium crookwellense",
    "Fusarium decemcellulare",
    "Fusarium dimerum",
    "Fusarium incarnatum",
    "Fusarium lateritium",
    "Fusarium longipes",
    "Fusarium merismoides",
    "Fusarium nygamai",
    "Fusarium pseudograminearum",
    "Fusarium scirpi",
    "Fusarium sacchari",
    "Fusarium thapsinum",
    "Fusarium venenatum",
    "Fusarium virguliforme",
    "Fusarium xylarioides",
]


def iter_fusarium_fallback() -> Generator[Tuple[dict, str, str], None, None]:
    """
    Fallback iterator using known Fusarium species list.
    Use when web scraping fails.
    """
    print(f"Using fallback list with {len(KNOWN_FUSARIUM_SPECIES)} known Fusarium species", flush=True)
    
    for i, name in enumerate(KNOWN_FUSARIUM_SPECIES):
        species_id = name.replace(" ", "_").lower()
        
        record = {
            "id": species_id,
            "scientific_name": name,
            "authority": None,
            "status": "accepted",
            "url": None,
            "common_name": None,
            "description": None,
            "type_strain": None,
            "basionym": None,
            "synonyms": [],
            "hosts": [],
            "distribution": None,
        }
        
        mapped = map_fusarium_record(record)
        yield mapped, "fusarium", species_id
