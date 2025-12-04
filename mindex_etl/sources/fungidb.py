from __future__ import annotations

from typing import Dict, Generator, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_page(client: httpx.Client, page: int, page_size: int) -> dict:
    resp = client.get(
        f"{settings.fungidb_base_url}/genomes",
        params={"page": page, "pageSize": page_size, "kingdom": "Fungi"},
        timeout=settings.http_timeout,
    )
    resp.raise_for_status()
    return resp.json()


def iter_fungidb_genomes(
    *,
    page_size: int = 100,
    max_pages: Optional[int] = None,
    client: Optional[httpx.Client] = None,
) -> Generator[Dict, None, None]:
    own_client = False
    if client is None:
        client = httpx.Client()
        own_client = True
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
    finally:
        if own_client:
            client.close()
