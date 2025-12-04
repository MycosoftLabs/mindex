from __future__ import annotations

import string
from typing import Dict, Generator, List, Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

MYCOBANK_API = "https://www.mycobank.org/Services/MycoBankNumberService.svc/json"


def map_record(record: dict) -> Tuple[dict, List[str], str]:
    mb_number = str(record.get("MycoBankNr"))
    synonyms = record.get("Synonyms") or []
    mapped = {
        "canonical_name": record.get("CurrentName") or record.get("Name"),
        "rank": record.get("Rank") or "species",
        "common_name": record.get("CommonNames"),
        "authority": record.get("Authors"),
        "description": record.get("Remarks"),
        "source": "mycobank",
        "metadata": {
            "mycobank_number": mb_number,
            "basionym": record.get("Basionym"),
            "publication": record.get("Reference"),
        },
    }
    return mapped, synonyms, mb_number


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _search(client: httpx.Client, term: str) -> List[dict]:
    resp = client.get(
        f"{MYCOBANK_API}/SearchSpecies",
        params={"Name": term, "Start": 0, "Limit": 500},
        timeout=settings.http_timeout,
    )
    resp.raise_for_status()
    return resp.json() or []


def iter_mycobank_taxa(
    *,
    prefixes: Optional[List[str]] = None,
    client: Optional[httpx.Client] = None,
) -> Generator[Tuple[dict, List[str], str], None, None]:
    prefixes = prefixes or list(string.ascii_lowercase)
    own_client = False
    if client is None:
        client = httpx.Client()
        own_client = True
    try:
        for prefix in prefixes:
            data = _search(client, f"{prefix}%")
            for record in data:
                yield map_record(record)
    finally:
        if own_client:
            client.close()
