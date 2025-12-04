from __future__ import annotations

import httpx
import respx

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
    with respx.mock as mock:
        mock.get(f"{settings.inat_base_url}/taxa").mock(
            side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
        )
        rows = list(inat.iter_fungi_taxa(per_page=1, max_pages=2))
    assert len(rows) == 1
    taxon, source, external_id = rows[0]
    assert taxon["canonical_name"] == "Agaricus"
    assert source == "inat"
    assert external_id == "1"
