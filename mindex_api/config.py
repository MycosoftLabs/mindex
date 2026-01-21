from typing import List, Optional, Union

import json

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    mindex_db_host: str = "localhost"
    mindex_db_port: int = 5432
    mindex_db_user: str = "mindex"
    mindex_db_password: str = "change-me"
    mindex_db_name: str = "mindex"

    # API
    api_title: str = "MINDEX API"
    api_version: str = "0.2.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_log_level: str = "info"
    api_prefix: str = "/api/mindex"

    api_cors_origins: List[AnyHttpUrl] = Field(default_factory=list)
    # NOTE: pydantic-settings is strict about parsing list env vars (expects JSON).
    # We accept either:
    # - a JSON list string: '["k1","k2"]'
    # - a single string: 'k1'
    # - a comma-separated string: 'k1,k2'
    # The Union[str, List[str]] avoids SettingsError when API_KEYS is provided as a plain string.
    api_keys: Union[List[str], str] = Field(default_factory=list, description="Allowed API keys for protected endpoints.")
    # Pagination defaults
    # NOTE: The website Explorer/Database wants to browse hundreds at a time.
    # The old max_page_size=100 caused the UI to "mysteriously" cap at 100 for every letter.
    default_page_size: int = 100
    max_page_size: int = 1000
    telemetry_latest_limit: int = 100

    @field_validator("api_keys", mode="before")
    @classmethod
    def parse_api_keys(cls, v):
        """
        Allow API_KEYS to be provided as:
        - JSON list: ["k1","k2"]
        - single string: "k1"
        - comma-separated: "k1,k2"
        """
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            raw = v.strip()
            if raw == "":
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    return parsed if isinstance(parsed, list) else [str(parsed)]
                except Exception:
                    # fall back to comma-separated
                    pass
            if "," in raw:
                return [p.strip() for p in raw.split(",") if p.strip()]
            return [raw]
        return v

    # Integrations
    hypergraph_endpoint: Optional[AnyHttpUrl] = None
    bitcoin_ordinal_endpoint: Optional[AnyHttpUrl] = None
    solana_rpc_url: Optional[AnyHttpUrl] = None

    # =========================================================================
    # MYCOBRAIN / MDP V1 SETTINGS
    # =========================================================================
    
    # Device management
    mycobrain_device_timeout_seconds: int = Field(
        120,
        description="Seconds before a device is considered 'stale' (no telemetry).",
    )
    mycobrain_device_offline_seconds: int = Field(
        600,
        description="Seconds before a device is considered 'offline'.",
    )
    
    # Telemetry ingestion
    mycobrain_max_batch_size: int = Field(
        1000,
        description="Maximum number of telemetry items in a batch ingest request.",
    )
    mycobrain_telemetry_retention_days: int = Field(
        90,
        description="Days to retain high-frequency telemetry data.",
    )
    
    # Command queue
    mycobrain_command_default_ttl_seconds: int = Field(
        3600,
        description="Default time-to-live for queued commands.",
    )
    mycobrain_command_max_retries: int = Field(
        3,
        description="Maximum retry attempts for failed commands.",
    )
    
    # MDP Protocol
    mdp_enable_raw_frame_logging: bool = Field(
        False,
        description="Log raw COBS frames for debugging.",
    )
    mdp_crc_strict_mode: bool = Field(
        True,
        description="Reject frames with invalid CRC (disable for debugging).",
    )
    
    # =========================================================================
    # MYCORRHIZAE PROTOCOL SETTINGS
    # =========================================================================
    
    mycorrhizae_default_channel_buffer: int = Field(
        100,
        description="Default message buffer size per channel.",
    )
    mycorrhizae_max_message_ttl_seconds: int = Field(
        86400,
        description="Maximum allowed TTL for published messages (24 hours).",
    )
    
    # =========================================================================
    # NATUREOS INTEGRATION
    # =========================================================================
    
    natureos_api_endpoint: Optional[AnyHttpUrl] = Field(
        None,
        description="NatureOS API endpoint for widget registration.",
    )
    natureos_webhook_secret: Optional[str] = Field(
        None,
        description="Shared secret for NatureOS webhook authentication.",
    )
    
    # =========================================================================
    # MAS (MYCOSOFT AGENT SERVICE) INTEGRATION
    # =========================================================================
    
    mas_api_endpoint: Optional[AnyHttpUrl] = Field(
        None,
        description="MAS API endpoint for agent coordination.",
    )
    mas_device_agent_enabled: bool = Field(
        False,
        description="Enable MAS device agent for LoRa/UART routing.",
    )

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
