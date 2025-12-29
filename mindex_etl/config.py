from __future__ import annotations

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


def _get_default_db_url() -> str:
    """Build database URL from environment or use defaults."""
    host = os.getenv("MINDEX_DB_HOST", "localhost")
    port = os.getenv("MINDEX_DB_PORT", "5434")  # Docker uses 5434 by default
    user = os.getenv("MINDEX_DB_USER", "mindex")
    password = os.getenv("MINDEX_DB_PASSWORD", "mindex")
    name = os.getenv("MINDEX_DB_NAME", "mindex")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


class ETLSettings(BaseSettings):
    # Database - uses synchronous psycopg for ETL jobs
    database_url: str = Field(
        default_factory=_get_default_db_url,
        description="Sync psycopg connection string.",
    )

    # HTTP settings
    http_timeout: int = 30
    http_retries: int = 3
    rate_limit_delay: float = 0.5  # Delay between API calls

    # iNaturalist
    inat_base_url: str = "https://api.inaturalist.org/v1"
    inat_rate_limit: float = 0.5  # Respect iNat API rate limits

    # MycoBank
    mycobank_base_url: str = "https://www.mycobank.org/Services/Generic/SearchService.svc/rest"

    # FungiDB
    fungidb_base_url: str = "https://fungidb.org/api"

    # Mushroom World
    mushroom_world_base_url: str = "https://mushroom.world/api"

    # Wikipedia
    wikipedia_api_url: str = "https://en.wikipedia.org/api/rest_v1/page/summary"

    # GBIF (Global Biodiversity Information Facility)
    gbif_base_url: str = "https://api.gbif.org/v1"

    # Index Fungorum
    index_fungorum_base_url: str = "http://www.indexfungorum.org"

    # Species Fungorum
    species_fungorum_base_url: str = "http://www.speciesfungorum.org"

    # MycoPortal (for North American fungi)
    mycoportal_base_url: str = "https://mycoportal.org/api"

    # Batch sizes
    batch_size: int = 100
    max_concurrent_requests: int = 5

    # Webhooks (optional)
    hypergraph_webhook: Optional[str] = None
    sync_complete_webhook: Optional[str] = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


settings = ETLSettings()
