from __future__ import annotations

from typing import Dict, Generator, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _fetch_species(client: httpx.Client, page: int, page_size: int) -> dict:
    resp = client.get(
        f"{settings.mushroom_world_base_url}/species",
        params={"page": page, "pageSize": page_size},
        timeout=settings.http_timeout,
    )
    resp.raise_for_status()
    return resp.json()


def iter_mushroom_world_species(
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
            payload = _fetch_species(client, page, page_size)
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
    finally:
        if own_client:
            client.close()
