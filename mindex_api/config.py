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
    api_version: str = "0.2.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_log_level: str = "info"
    api_prefix: str = "/api/mindex"

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
