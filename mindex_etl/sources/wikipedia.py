from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import quote

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..config import settings


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
def fetch_page_summary(title: str, client: Optional[httpx.Client] = None) -> Dict:
    close_client = False
    if client is None:
        client = httpx.Client()
        close_client = True
    try:
        resp = client.get(
            f"{settings.wikipedia_api_url}/{quote(title)}",
            timeout=settings.http_timeout,
            headers={"User-Agent": "mindex-etl/0.1"},
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()
    finally:
        if close_client:
            client.close()


def extract_traits(summary: Dict) -> Dict[str, str]:
    traits = {}
    infobox = summary.get("infobox") or {}
    for key in ("ecology", "edibility", "cap shape", "hymenium type"):
        value = infobox.get(key)
        if value:
            traits[key] = value
    if summary.get("description"):
        traits["description"] = summary["description"]
    return traits
