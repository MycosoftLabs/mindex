from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class ETLSettings(BaseSettings):
    database_url: str = Field(
        default="postgresql://mindex:mindex@localhost:5432/mindex",
        description="Sync psycopg connection string.",
    )
    http_timeout: int = 30
    http_retries: int = 3
    inat_base_url: str = "https://api.inaturalist.org/v1"
    mycobank_base_url: str = "https://www.mycobank.org/Services/Generic/SearchService.svc/rest"
    fungidb_base_url: str = "https://fungidb.org/api"
    mushroom_world_base_url: str = "https://mushroom.world/api"
    wikipedia_api_url: str = "https://en.wikipedia.org/api/rest_v1/page/summary"

    hypergraph_webhook: Optional[str] = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


settings = ETLSettings()
