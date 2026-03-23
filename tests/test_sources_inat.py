from __future__ import annotations

import httpx
from unittest.mock import patch

from mindex_etl.config import settings
from mindex_etl.sources import inat


def test_iter_fungi_taxa_handles_pagination():
    page1 = {
        "results": [
            {
                "id": 1,
                "name": "Agaricus",
                "rank": "species",
                "preferred_common_name": "Field mushroom",
                "wikipedia_summary": "Summary",
            }
        ]
    }
    page2 = {"results": []}
    with patch("httpx.Client.get") as mock_get:
        mock_get.side_effect = [
            httpx.Response(200, json=page1, request=httpx.Request("GET", "")), 
            httpx.Response(200, json=page2, request=httpx.Request("GET", ""))
        ]
        rows = list(inat.iter_fungi_taxa(per_page=1, max_pages=2))
    assert len(rows) == 1
    taxon, source, external_id = rows[0]
    assert taxon["canonical_name"] == "Agaricus"
    assert source == "inat"
    assert external_id == "1"
