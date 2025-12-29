"""
Index Fungorum Data Source
==========================
Fetch fungal nomenclature data from Index Fungorum.
http://www.indexfungorum.org/
"""
from __future__ import annotations

import time
from typing import Dict, Generator, List, Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

INDEX_FUNGORUM_SEARCH = "http://www.indexfungorum.org/Names/Names.asp"
INDEX_FUNGORUM_DETAIL = "http://www.indexfungorum.org/Names/NamesRecord.asp"


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _search_names(client: httpx.Client, search_term: str, page: int = 1) -> List[dict]:
    """Search Index Fungorum for names matching the search term."""
    try:
        resp = client.get(
            INDEX_FUNGORUM_SEARCH,
            params={
                "SearchTerm": search_term,
                "PageSize": 50,
                "PageNo": page,
            },
            timeout=settings.http_timeout,
            headers={"User-Agent": "mindex-etl/0.1"},
        )
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    results = []

    # Parse the HTML table of results
    table = soup.find("table", class_="Results")
    if not table:
        return []

    for row in table.find_all("tr")[1:]:  # Skip header
        cols = row.find_all("td")
        if len(cols) >= 3:
            link = cols[0].find("a")
            if link:
                record_id = link.get("href", "").split("=")[-1]
                results.append({
                    "id": record_id,
                    "name": cols[0].get_text(strip=True),
                    "author": cols[1].get_text(strip=True) if len(cols) > 1 else None,
                    "year": cols[2].get_text(strip=True) if len(cols) > 2 else None,
                })

    return results


def map_index_fungorum(record: dict) -> dict:
    """Map Index Fungorum record to MINDEX taxon format."""
    return {
        "canonical_name": record.get("name"),
        "rank": "species",  # Default, would need parsing for higher ranks
        "authority": record.get("author"),
        "description": None,
        "source": "index_fungorum",
        "metadata": {
            "if_id": record.get("id"),
            "year": record.get("year"),
            "current_name": record.get("current_name"),
            "basionym": record.get("basionym"),
        },
    }


def iter_index_fungorum_names(
    *,
    prefixes: Optional[List[str]] = None,
    max_pages: Optional[int] = None,
    delay_seconds: float = 1.0,
) -> Generator[Dict, None, None]:
    """Iterate through Index Fungorum names."""
    import string

    prefixes = prefixes or list(string.ascii_lowercase)

    with httpx.Client() as client:
        for prefix in prefixes:
            page = 1
            while True:
                try:
                    results = _search_names(client, f"{prefix}*", page)
                except Exception:
                    break

                if not results:
                    break

                for record in results:
                    if record.get("name"):
                        yield map_index_fungorum(record), str(record.get("id", ""))

                page += 1
                if max_pages and page > max_pages:
                    break

                time.sleep(delay_seconds)
