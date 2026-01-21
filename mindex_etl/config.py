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
    password = os.getenv("MINDEX_DB_PASSWORD", "change-me")
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

    # Local data storage for scraping (use local first, then NAS)
    local_data_dir: str = Field(
        default="C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_scrape",
        description="Local directory for scraped data downloads"
    )
    nas_data_dir: str = Field(
        default="\\\\192.168.1.50\\mindex",
        description="NAS directory for large data storage"
    )

    # iNaturalist
    inat_base_url: str = "https://api.inaturalist.org/v1"
    inat_api_token: str = Field(
        default="eyJhbGciOiJIUzUxMiJ9.eyJ1c2VyX2lkIjoxMDAxOTc2OSwiZXhwIjoxNzY1OTE1MDY2fQ.JXV3lLOyuuXeItfNUagixJCtKN3SI20_em1sl2gKFFDppHBNJXy79x6I6jJbiPG1a6n_-cj1JgysSmuKlbDKVg",
        description="iNaturalist API JWT token for authenticated requests"
    )
    inat_rate_limit: float = 0.3  # Faster with API token (3 req/sec)

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
        "extra": "ignore",  # Ignore extra environment variables
    }


settings = ETLSettings()
