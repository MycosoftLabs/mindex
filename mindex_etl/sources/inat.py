from __future__ import annotations

import time
from typing import Dict, Generator, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings

FUNGI_TAXON_ID = 47170


def map_inat_taxon(record: dict) -> dict:
    return {
        "canonical_name": record.get("name"),
        "rank": record.get("rank") or "species",
        "common_name": record.get("preferred_common_name"),
        "description": record.get("wikipedia_summary"),
        "source": "inat",
        "metadata": {
            "inat_id": record.get("id"),
            "parent_id": record.get("parent_id"),
            "ancestry": record.get("ancestry"),
            "observations_count": record.get("observations_count"),
            "wikipedia_url": record.get("wikipedia_url"),
        },
    }


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_page(client: httpx.Client, page: int, per_page: int) -> dict:
    response = client.get(
        f"{settings.inat_base_url}/taxa",
        params={
            "taxon_id": FUNGI_TAXON_ID,
            "rank": "species",
            "is_active": True,
            "order_by": "observations_count",
            "per_page": per_page,
            "page": page,
        },
        timeout=settings.http_timeout,
        headers={"User-Agent": "mindex-etl/0.1"},
    )
    response.raise_for_status()
    return response.json()


def iter_fungi_taxa(
    *,
    per_page: int = 100,
    max_pages: Optional[int] = None,
    delay_seconds: float = 0.2,
    client: Optional[httpx.Client] = None,
) -> Generator[Dict, None, None]:
    per_page = min(per_page, 200)
    close_client = False
    if client is None:
        client = httpx.Client()
        close_client = True
    try:
        page = 1
        while True:
            payload = _fetch_page(client, page, per_page)
            results = payload.get("results", [])
            if not results:
                break
            for record in results:
                mapped = map_inat_taxon(record)
                external_id = record.get("id")
                yield mapped, "inat", str(external_id)
            page += 1
            if max_pages and page > max_pages:
                break
            time.sleep(delay_seconds)
    finally:
        if close_client:
            client.close()
