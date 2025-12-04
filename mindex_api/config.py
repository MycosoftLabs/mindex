from typing import List, Optional

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    mindex_db_host: str = "localhost"
    mindex_db_port: int = 5432
    mindex_db_user: str = "mindex"
    mindex_db_password: str = "mindex"
    mindex_db_name: str = "mindex"

    # API
    api_title: str = "MINDEX API"
    api_version: str = "0.1.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_log_level: str = "info"

    api_cors_origins: List[AnyHttpUrl] = Field(default_factory=list)
    api_keys: List[str] = Field(
        default_factory=list,
        description="Allowed API keys for protected endpoints.",
    )
    default_page_size: int = 25
    max_page_size: int = 100
    telemetry_latest_limit: int = 100

    # Integrations
    hypergraph_endpoint: Optional[AnyHttpUrl] = None
    bitcoin_ordinal_endpoint: Optional[AnyHttpUrl] = None
    solana_rpc_url: Optional[AnyHttpUrl] = None

    @property
    def mindex_db_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.mindex_db_user}:"
            f"{self.mindex_db_password}@{self.mindex_db_host}:"
            f"{self.mindex_db_port}/{self.mindex_db_name}"
        )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


settings = Settings()
