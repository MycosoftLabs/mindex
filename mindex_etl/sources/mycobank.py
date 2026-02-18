"""
MycoBank Species Scraper

Multi-strategy scraper for MycoBank fungal nomenclature database.
Supports: API, web scraping, and data dump downloads.

Target: 545,007+ fungal species
Source: https://www.mycobank.org
"""

from __future__ import annotations

import csv
import json
import os
import re
import string
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, wait_fixed, retry_if_exception_type

from ..config import settings

MYCOBANK_BASE_URL = "https://www.mycobank.org"
MYCOBANK_API = f"{MYCOBANK_BASE_URL}/Services/MycoBankNumberService.svc/json"
MYCOBANK_SEARCH = f"{MYCOBANK_BASE_URL}/Basic%20names%20search"

# Data dump URLs (if available)
MYCOBANK_DUMP_URLS = [
    # Modern MycoBank export referenced from the UI (contains MBList.xlsx)
    "https://www.mycobank.org/images/MBList.zip",
    "https://www.MycoBank.org/images/MBList.zip",
    # Legacy URLs (often 404)
    "https://www.mycobank.org/downloads/MycoBank_dump.zip",
    "https://www.mycobank.org/downloads/names.csv",
]


def map_record(record: dict) -> Tuple[dict, List[str], str]:
    """Map MycoBank record to MINDEX taxon format."""
    mb_number = str(record.get("MycoBankNr") or record.get("id", ""))
    synonyms = record.get("Synonyms") or []
    
    mapped = {
        "canonical_name": record.get("CurrentName") or record.get("Name") or record.get("name"),
        "rank": record.get("Rank") or "species",
        "common_name": record.get("CommonNames"),
        "authority": record.get("Authors") or record.get("authority"),
        "description": record.get("Remarks") or record.get("description"),
        "source": "mycobank",
        "metadata": {
            "mycobank_number": mb_number,
            "basionym": record.get("Basionym"),
            "publication": record.get("Reference") or record.get("publication"),
            "year": record.get("Year") or record.get("year"),
            "type_species": record.get("TypeSpecies"),
            "classification": record.get("Classification"),
        },
    }
    return mapped, synonyms, mb_number


def save_to_local(data: list, filename: str, subdir: str = "mycobank") -> str:
    """Save scraped data to local storage."""
    data_dir = Path(settings.local_data_dir) / subdir
    data_dir.mkdir(parents=True, exist_ok=True)
    
    filepath = data_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    return str(filepath)


# =============================================================================
# STRATEGY 1: API-based fetching (may have issues)
# =============================================================================

def _api_search(client: httpx.Client, term: str) -> List[dict]:
    """Search MycoBank via JSON API."""
    # Retry only on transient network errors; do not retry 406/403 blocks.
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        retry=retry_if_exception_type((httpx.RequestError,)),
        reraise=True,
    )
    def _do() -> httpx.Response:
        return client.get(
            f"{MYCOBANK_API}/SearchSpecies",
            params={"Name": term, "Start": 0, "Limit": 500},
            timeout=settings.http_timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": MYCOBANK_BASE_URL + "/",
            },
        )

    resp = _do()
    # MycoBank frequently responds with 406 when automated clients are blocked.
    if resp.status_code == 406:
        raise httpx.HTTPStatusError("MycoBank API returned 406 (blocked)", request=resp.request, response=resp)
    resp.raise_for_status()
    
    # Handle empty or invalid responses
    content = resp.text.strip()
    if not content or content == "null":
        return []
    
    try:
        return resp.json() or []
    except json.JSONDecodeError:
        print(f"Invalid JSON response for term '{term}'", flush=True)
        return []


def iter_mycobank_api(
    *,
    prefixes: Optional[List[str]] = None,
    client: Optional[httpx.Client] = None,
) -> Generator[Tuple[dict, List[str], str], None, None]:
    """Iterate via MycoBank API."""
    prefixes = prefixes or list(string.ascii_lowercase)
    own_client = False
    if client is None:
        client = httpx.Client()
        own_client = True
    
    try:
        for prefix in prefixes:
            print(f"[API] Fetching prefix '{prefix}'...", flush=True)
            try:
                data = _api_search(client, f"{prefix}%")
                for record in data:
                    yield map_record(record)
                print(f"[API] Found {len(data)} records for '{prefix}'", flush=True)
            except Exception as e:
                print(f"[API] Error for prefix '{prefix}': {e}", flush=True)
                continue
            time.sleep(1)  # Rate limit
    finally:
        if own_client:
            client.close()


# =============================================================================
# STRATEGY 2: Web scraping (more reliable)
# =============================================================================

def get_scraper_headers() -> dict:
    """Get headers that mimic a browser."""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=4, max=60),
)
def _scrape_search_page(client: httpx.Client, term: str, page: int = 1) -> Tuple[List[dict], bool]:
    """Scrape MycoBank search results page."""
    params = {
        "Name": term,
        "page": page,
    }
    
    response = client.get(
        MYCOBANK_SEARCH,
        params=params,
        timeout=60.0,
        headers=get_scraper_headers(),
    )
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    records = []
    
    # Find result table/list
    # MycoBank typically shows results in a table
    results_container = soup.select_one(
        "#SearchResults, .results-table, table.data, .search-results"
    )
    
    if not results_container:
        # Try to find any table with species data
        results_container = soup.select_one("table")
    
    if results_container:
        rows = results_container.select("tr")
        
        for row in rows[1:]:  # Skip header
            cells = row.select("td")
            if not cells:
                continue
            
            try:
                # Extract name from first cell
                name_cell = cells[0]
                name_link = name_cell.select_one("a")
                name = name_link.get_text(strip=True) if name_link else name_cell.get_text(strip=True)
                
                if not name or len(name) < 3:
                    continue
                
                # Extract MB number from link
                mb_number = ""
                if name_link and name_link.get("href"):
                    href = name_link["href"]
                    mb_match = re.search(r"MB/(\d+)|MycoBankNr=(\d+)", href)
                    if mb_match:
                        mb_number = mb_match.group(1) or mb_match.group(2)
                
                # Extract authors
                authors = cells[1].get_text(strip=True) if len(cells) > 1 else None
                
                # Extract year
                year = cells[2].get_text(strip=True) if len(cells) > 2 else None
                
                # Extract rank
                rank = cells[3].get_text(strip=True).lower() if len(cells) > 3 else "species"
                
                record = {
                    "name": name,
                    "id": mb_number or name.replace(" ", "_").lower(),
                    "authority": authors,
                    "year": year,
                    "Rank": rank,
                }
                
                records.append(record)
                
            except Exception as e:
                print(f"Error parsing row: {e}")
                continue
    
    # Check for next page
    has_more = False
    pagination = soup.select_one(".pagination, .pager, nav.pages")
    if pagination:
        next_link = pagination.select_one("a.next, a[rel='next'], .next-page")
        has_more = next_link is not None
    
    return records, has_more


def iter_mycobank_scrape(
    *,
    prefixes: Optional[List[str]] = None,
    max_pages_per_prefix: int = 100,
    delay_seconds: float = 2.0,
    client: Optional[httpx.Client] = None,
) -> Generator[Tuple[dict, List[str], str], None, None]:
    """Iterate via web scraping."""
    prefixes = prefixes or list(string.ascii_lowercase)
    own_client = False
    if client is None:
        client = httpx.Client(follow_redirects=True)
        own_client = True
    
    seen_names = set()
    
    try:
        for prefix in prefixes:
            print(f"[SCRAPE] Scraping prefix '{prefix}'...", flush=True)
            
            page = 1
            prefix_count = 0
            
            while page <= max_pages_per_prefix:
                try:
                    records, has_more = _scrape_search_page(client, f"{prefix}*", page)
                    
                    for record in records:
                        name = record.get("name", "").lower()
                        if name in seen_names:
                            continue
                        seen_names.add(name)
                        
                        mapped, synonyms, ext_id = map_record(record)
                        yield mapped, synonyms, ext_id
                        prefix_count += 1
                    
                    if not has_more or not records:
                        break
                    
                    page += 1
                    time.sleep(delay_seconds)
                    
                except Exception as e:
                    print(f"[SCRAPE] Error on page {page} for '{prefix}': {e}")
                    break
            
            print(f"[SCRAPE] Found {prefix_count} records for '{prefix}'", flush=True)
            time.sleep(delay_seconds)
            
    finally:
        if own_client:
            client.close()


# =============================================================================
# STRATEGY 3: Data dump download (most complete)
# =============================================================================

def download_mycobank_dump(output_dir: str = None) -> Optional[str]:
    """
    Download MycoBank data dump if available.
    
    Returns:
        Path to downloaded file or None if not available
    """
    output_dir = output_dir or str(Path(settings.local_data_dir) / "mycobank")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    print("="*60)
    print("ATTEMPTING MYCOBANK DATA DUMP DOWNLOAD")
    print("="*60)
    
    with httpx.Client(follow_redirects=True, headers=get_scraper_headers()) as client:
        for url in MYCOBANK_DUMP_URLS:
            try:
                print(f"Trying: {url}")
                # Some endpoints may not support HEAD reliably; try GET headers fallback.
                response = client.head(url, timeout=30.0)
                
                if response.status_code == 200:
                    # Download the file
                    filename = url.split("/")[-1]
                    filepath = Path(output_dir) / filename
                    
                    print(f"Downloading {filename}...")
                    with client.stream("GET", url, timeout=600.0) as r:
                        r.raise_for_status()
                        with open(filepath, "wb") as f:
                            for chunk in r.iter_bytes(chunk_size=8192):
                                f.write(chunk)
                    
                    print(f"Downloaded to: {filepath}")
                    return str(filepath)
                elif response.status_code in (403, 404, 406):
                    # Not available / blocked
                    print(f"  Not available (HTTP {response.status_code})")
                    
            except Exception as e:
                print(f"  Failed: {e}")
                continue
    
    print("Data dump not available, will use web scraping")
    return None


def parse_mycobank_csv(filepath: str) -> Generator[Tuple[dict, List[str], str], None, None]:
    """Parse MycoBank CSV dump file."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            record = {
                "Name": row.get("Name") or row.get("CurrentName") or row.get("FullName"),
                "MycoBankNr": row.get("MycoBankNr") or row.get("MB#") or row.get("ID"),
                "Authors": row.get("Authors") or row.get("Authority"),
                "Rank": row.get("Rank") or row.get("TaxonRank") or "species",
                "Year": row.get("Year") or row.get("PublicationYear"),
                "Reference": row.get("Reference") or row.get("Publication"),
                "Basionym": row.get("Basionym"),
                "Synonyms": (row.get("Synonyms") or "").split(";") if row.get("Synonyms") else [],
            }
            
            yield map_record(record)


def parse_mycobank_xlsx(filepath: str) -> Generator[Tuple[dict, List[str], str], None, None]:
    """
    Parse MycoBank XLSX dump.

    MycoBank currently publishes MBList.zip -> MBList.xlsx.
    We stream rows using openpyxl (read_only=True) to avoid large memory usage.
    """
    try:
        import openpyxl  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "openpyxl is required to parse MycoBank XLSX dumps. Install: pip install openpyxl"
        ) from e

    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.worksheets[0]

    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        return

    def norm(s: object) -> str:
        if s is None:
            return ""
        return re.sub(r"[^a-z0-9]+", "", str(s).strip().lower())

    headers = [norm(h) for h in header]

    def find_col(candidates: list[str]) -> Optional[int]:
        cand_norm = [norm(c) for c in candidates]
        for i, h in enumerate(headers):
            if not h:
                continue
            for c in cand_norm:
                if c and (h == c or c in h):
                    return i
        return None

    idx_name = find_col(["Name", "Taxon name", "CurrentName", "FullName", "Full name"])
    idx_mb = find_col(["MycoBankNr", "MycoBank #", "MB#", "MB number", "MycoBank number"])
    idx_auth = find_col(["Authors", "Authority", "Author"])
    idx_rank = find_col(["Rank", "TaxonRank", "Taxon rank"])
    idx_year = find_col(["Year", "PublicationYear", "Publication year"])

    if idx_name is None:
        raise RuntimeError(f"MycoBank XLSX: could not find a name column in headers: {headers[:30]}")

    for row in rows:
        try:
            name = row[idx_name] if idx_name is not None else None
            if not name:
                continue
            mb = row[idx_mb] if idx_mb is not None else None
            authors = row[idx_auth] if idx_auth is not None else None
            rank = row[idx_rank] if idx_rank is not None else None
            year = row[idx_year] if idx_year is not None else None

            record = {
                "Name": str(name).strip(),
                "MycoBankNr": str(mb).strip() if mb is not None and str(mb).strip() else None,
                "Authors": str(authors).strip() if authors is not None and str(authors).strip() else None,
                "Rank": str(rank).strip() if rank is not None and str(rank).strip() else None,
                "Year": str(year).strip() if year is not None and str(year).strip() else None,
            }
            yield map_record(record)
        except Exception:
            continue


def _find_first_csv_in_zip(zip_path: str) -> Optional[str]:
    """Return the first CSV-like member name from a ZIP file."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            lname = name.lower()
            if lname.endswith(".csv") or lname.endswith(".tsv") or lname.endswith(".xlsx") or lname.endswith(".txt"):
                return name
    return None


def parse_mycobank_zip(zip_path: str, output_dir: Optional[str] = None) -> Generator[Tuple[dict, List[str], str], None, None]:
    """
    Parse a MycoBank ZIP dump by extracting the first CSV/TSV/XLSX we can find.
    """
    output_dir = output_dir or str(Path(settings.local_data_dir) / "mycobank" / "extracted")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    member = _find_first_csv_in_zip(zip_path)
    if not member:
        print("No CSV/TSV found inside MycoBank ZIP dump", flush=True)
        return

    with zipfile.ZipFile(zip_path, "r") as zf:
        extracted_path = str(Path(output_dir) / Path(member).name)
        with zf.open(member, "r") as src, open(extracted_path, "wb") as dst:
            dst.write(src.read())
    lower = extracted_path.lower()
    if lower.endswith(".xlsx"):
        yield from parse_mycobank_xlsx(extracted_path)
    else:
        # Delegate to CSV parser (utf-8-sig handling). If this isn't a CSV, it'll fail fast.
        yield from parse_mycobank_csv(extracted_path)


# =============================================================================
# SMART ITERATOR: Tries all strategies
# =============================================================================

def iter_mycobank_taxa(
    *,
    prefixes: Optional[List[str]] = None,
    client: Optional[httpx.Client] = None,
    use_scraping: bool = True,
    try_dump: bool = True,
    save_locally: bool = True,
) -> Generator[Tuple[dict, List[str], str], None, None]:
    """
    Smart iterator that tries multiple strategies:
    1. Data dump (if available)
    2. Web scraping (slower but reliable)
    3. API (fast but may fail)
    
    Args:
        prefixes: Letter prefixes to search (a-z by default)
        client: Optional HTTP client
        use_scraping: Fall back to web scraping if API fails
        try_dump: Try to download data dump first
        save_locally: Save scraped data to local storage
    
    Yields:
        Tuple of (mapped_taxon, synonyms, external_id)
    """
    all_records = []
    
    # Strategy 1: Try data dump first
    if try_dump:
        dump_path = download_mycobank_dump()
        if dump_path:
            print(f"Using data dump: {dump_path}")
            try:
                if dump_path.endswith(".csv"):
                    for item in parse_mycobank_csv(dump_path):
                        yield item
                        if save_locally:
                            all_records.append(item)
                    return
                if dump_path.endswith(".zip"):
                    for item in parse_mycobank_zip(dump_path):
                        yield item
                        if save_locally:
                            all_records.append(item)
                    return
            except Exception as e:
                print(f"Failed to parse dump '{dump_path}': {e}", flush=True)
    
    # Strategy 2: Web scraping (prefer over API because API often returns 406)
    if use_scraping:
        print("Falling back to web scraping...")
        
        for item in iter_mycobank_scrape(prefixes=prefixes, client=client):
            yield item
            if save_locally:
                all_records.append(item)

        # If scraping worked at all, do not attempt API.
        if all_records:
            return

    # Strategy 3: Try API last (often blocked)
    print("Trying API method (last resort)...")
    try:
        for item in iter_mycobank_api(prefixes=prefixes, client=client):
            yield item
            if save_locally:
                all_records.append(item)
    except Exception as e:
        print(f"API method failed: {e}", flush=True)
    
    # Save all records locally
    if save_locally and all_records:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"mycobank_taxa_{timestamp}.json"
        filepath = save_to_local(
            [{"taxon": t, "synonyms": s, "id": i} for t, s, i in all_records],
            filename
        )
        print(f"Saved {len(all_records)} records to {filepath}")


def download_all_mycobank_data(output_dir: str = None) -> str:
    """
    Download ALL MycoBank data to local storage.
    
    Uses aggressive scraping to get everything.
    
    Args:
        output_dir: Directory to save data
    
    Returns:
        Path to saved JSON file
    """
    output_dir = output_dir or settings.local_data_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    print("="*60)
    print("DOWNLOADING ALL MYCOBANK FUNGI DATA")
    print("="*60)
    
    all_taxa = []
    
    for taxon, synonyms, ext_id in iter_mycobank_taxa(
        prefixes=list(string.ascii_lowercase),
        use_scraping=True,
        try_dump=True,
        save_locally=False,
    ):
        all_taxa.append({
            "taxon": taxon,
            "synonyms": synonyms,
            "external_id": ext_id,
        })
    
    # Save complete dump
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"mycobank_complete_{timestamp}.json"
    filepath = Path(output_dir) / "mycobank" / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({
            "downloaded_at": datetime.now().isoformat(),
            "total_taxa": len(all_taxa),
            "taxa": all_taxa,
        }, f, indent=2, ensure_ascii=False)
    
    print("="*60)
    print(f"COMPLETE: Downloaded {len(all_taxa)} total taxa")
    print(f"Saved to: {filepath}")
    print("="*60)
    
    return str(filepath)
