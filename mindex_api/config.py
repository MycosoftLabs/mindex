import os
from typing import List, Optional, Union

import json

from pydantic import AliasChoices, AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    mindex_db_host: str = "localhost"
    mindex_db_port: int = 5432
    mindex_db_user: str = "mindex"
    mindex_db_password: str = "mindex"
    mindex_db_name: str = "mindex"
    mindex_db_pool_size: int = Field(
        20,
        description="SQLAlchemy async pool size (default was too small for concurrent bulk ingests).",
    )
    mindex_db_max_overflow: int = Field(
        20,
        description="Extra connections beyond pool_size when load spikes.",
    )
    mindex_db_pool_recycle_seconds: int = Field(
        3600,
        description="Recycle connections after this many seconds (avoids stale server-side sessions).",
    )

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
    # Accept either the historical env var `API_KEYS` or the clearer `MINDEX_API_KEY(S)`.
    api_keys: Union[List[str], str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("API_KEYS", "MINDEX_API_KEYS", "MINDEX_API_KEY"),
        description="Allowed API keys for protected endpoints.",
    )
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

    # =========================================================================
    # SUPABASE (Cloud Middleware)
    # =========================================================================

    supabase_url: Optional[str] = Field(
        default=None,
        description="Supabase project URL (e.g., https://xxx.supabase.co). Set SUPABASE_URL env var.",
    )
    supabase_anon_key: Optional[str] = Field(
        default=None,
        description="Supabase anon/public key for client-side access.",
    )
    supabase_service_role_key: Optional[str] = Field(
        default=None,
        description="Supabase service role key for server-side operations (upserts, storage, realtime).",
    )

    # =========================================================================
    # REDIS (Cache Layer)
    # =========================================================================

    redis_url: Optional[str] = Field(
        default=None,
        description="Redis URL (e.g., redis://localhost:6379/0). Falls back to in-process LRU if not set.",
    )
    redis_search_ttl: int = Field(120, description="TTL for cached search results (seconds)")
    redis_entity_ttl: int = Field(600, description="TTL for cached entities (seconds)")
    redis_map_ttl: int = Field(60, description="TTL for cached map layer tiles (seconds)")

    # =========================================================================
    # NAS (Cold Storage - Ubiquiti)
    # =========================================================================

    nas_mount_path: str = Field(
        default="/mnt/nas/mindex",
        description="NAS mount point for cold storage (16TB base, expandable to ~178TB).",
    )
    local_staging_path: str = Field(
        default="/mnt/nas/mindex/staging",
        description="Staging area for data before durable NAS write.",
    )

    # Integrations
    hypergraph_endpoint: Optional[AnyHttpUrl] = None
    bitcoin_ordinal_endpoint: Optional[AnyHttpUrl] = None
    solana_rpc_url: Optional[AnyHttpUrl] = None

    # -------------------------------------------------------------------------
    # MINDEX App Overhaul (May 03, 2026) — chain + federation (env-only secrets)
    # -------------------------------------------------------------------------
    solana_keypair_path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("SOLANA_KEYPAIR_PATH"),
        description="Path to Solana keypair JSON on the API host (never commit the file).",
    )
    solana_network: str = Field(
        default="mainnet-beta",
        validation_alias=AliasChoices("SOLANA_NETWORK"),
        description="Solana cluster name for status reporting.",
    )
    btc_ordinals_wallet: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("BTC_ORDINALS_WALLET"),
        description="Ordinals-capable wallet address or descriptor handle (no WIF in env files that ship to git).",
    )
    bitcoin_rpc_url: Optional[AnyHttpUrl] = Field(
        default=None,
        validation_alias=AliasChoices("BITCOIN_RPC_URL"),
        description="Optional Bitcoin Core RPC base URL.",
    )
    p1_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("P1_API_KEY"),
        description="Platform One API key (set only in .env on secure hosts).",
    )
    p1_base_url: Optional[AnyHttpUrl] = Field(
        default=None,
        validation_alias=AliasChoices("P1_BASE_URL"),
        description="Platform One API base URL.",
    )
    avani_api_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AVANI_API_URL"),
        description="MAS or standalone AVANI base URL for Worldview governance review.",
    )
    avani_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AVANI_API_KEY", "MINDEX_AVANI_API_KEY"),
        description="Scoped AVANI API key with avani:evaluate for Worldview governance.",
    )
    nas_host: str = Field(
        default="192.168.0.105",
        validation_alias=AliasChoices("NAS_HOST"),
        description="Primary UniFi NAS host for federation metadata.",
    )
    aws_s3_mindex_bucket: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AWS_S3_MINDEX_BUCKET"),
        description="S3 bucket used for MINDEX cold copies / federation.",
    )
    prometheus_pushgateway_url: Optional[AnyHttpUrl] = Field(
        default=None,
        validation_alias=AliasChoices("PROMETHEUS_PUSHGATEWAY_URL"),
        description="Optional Prometheus pushgateway for MINDEX job metrics.",
    )
    prometheus_metrics_path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("PROMETHEUS_METRICS_PATH"),
        description="Optional filesystem path for node_exporter textfile metrics.",
    )

    # =========================================================================
    # API COMPARTMENTALIZATION — Internal / Worldview zones
    # =========================================================================

    worldview_prefix: str = Field(
        "/api/worldview/v1",
        description="URL prefix for the Worldview API (external, paying users).",
    )
    internal_prefix: str = Field(
        "/api/mindex/internal",
        description="URL prefix for internal service-to-service APIs.",
    )

    # Internal service auth — HMAC secret for signed tokens
    # Env must match bridge/deploy scripts: MINDEX_INTERNAL_SECRET (not only INTERNAL_AUTH_SECRET).
    internal_auth_secret: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("MINDEX_INTERNAL_SECRET", "INTERNAL_AUTH_SECRET"),
        description="HMAC-SHA256 shared secret for internal service tokens.",
    )
    # Internal service auth — pre-shared token list (simpler alternative)
    internal_tokens: Union[List[str], str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("MINDEX_INTERNAL_TOKENS", "MINDEX_INTERNAL_TOKEN"),
        description="Pre-shared internal service tokens (comma-separated or JSON list).",
    )

    @field_validator("internal_tokens", mode="before")
    @classmethod
    def parse_internal_tokens(cls, v):
        """Parse internal tokens the same way as api_keys."""
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
                    pass
            if "," in raw:
                return [p.strip() for p in raw.split(",") if p.strip()]
            return [raw]
        return v

    # Rate limiting
    rate_limit_enabled: bool = Field(True, description="Enable per-key rate limiting on Worldview API.")
    rate_limit_redis_prefix: str = Field("ratelimit", description="Redis key prefix for rate limit counters.")

    # Worldview CORS (separate from internal CORS)
    worldview_cors_origins: List[str] = Field(
        default_factory=lambda: ["https://mycosoft.com", "https://www.mycosoft.com"],
        description="Allowed CORS origins for the Worldview API.",
    )

    # Plan-tier rate limit defaults
    worldview_rate_limits: dict = Field(
        default_factory=lambda: {
            "free": {"per_minute": 10, "per_day": 1000},
            "pro": {"per_minute": 60, "per_day": 10000},
            "enterprise": {"per_minute": 300, "per_day": 100000},
        },
        description="Default rate limits per plan tier.",
    )

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
    natureos_api_key: Optional[str] = Field(
        None,
        description="Optional API key/header value used when forwarding telemetry from MINDEX to NatureOS.",
    )
    natureos_ingest_path: str = Field(
        "/api/mycobrain/telemetry/envelope",
        description="NatureOS ingest route used by MINDEX telemetry fanout.",
    )
    fusarium_fanout_enabled: bool = Field(
        True,
        description="When true, MINDEX mirrors incoming telemetry into Fusarium analytics tables.",
    )
    
    # =========================================================================
    # INAT / CREP NATURE CACHE (iNaturalist warm-cache ingest)
    # =========================================================================

    inat_api_base: str = Field(
        "https://api.inaturalist.org/v1",
        description="iNaturalist API base URL (v1).",
    )
    inat_api_token: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("INAT_API_TOKEN", "MINDEX_INAT_API_TOKEN"),
        description="Optional bearer token for iNaturalist (higher rate limits).",
    )

    # =========================================================================
    # MAS (MYCOSOFT AGENT SERVICE) INTEGRATION
    # =========================================================================
    
    mas_api_endpoint: Optional[AnyHttpUrl] = Field(
        None,
        validation_alias=AliasChoices("MAS_API_URL", "MAS_API_ENDPOINT", "MINDEX_MAS_API_URL"),
        description="MAS API endpoint for agent coordination.",
    )
    mas_device_agent_enabled: bool = Field(
        False,
        description="Enable MAS device agent for LoRa/UART routing.",
    )

    # =========================================================================
    # GPU ACCELERATION (cuDF + cuVS + STATIC)
    # =========================================================================

    gpu_enabled: bool = Field(
        False,
        description="Enable GPU acceleration via RAPIDS (cuDF, cuVS). "
        "Gracefully falls back to CPU when hardware/packages unavailable.",
    )
    gpu_device_id: int = Field(0, description="CUDA device ordinal.")
    gpu_memory_limit_gb: float = Field(
        0.0,
        description="GPU memory limit in GB (0 = auto, uses 80%% of VRAM).",
    )

    # cuVS vector index settings
    cuvs_index_dir: str = Field(
        "/mnt/nas/mindex/indexes/cuvs",
        description="Persistent NAS-backed storage for cuVS vector indexes.",
    )

    # STATIC constrained decoding (via MAS)
    static_enabled: bool = Field(
        False,
        description="Enable STATIC constrained decoding integration.",
    )
    static_mas_endpoint: Optional[str] = Field(
        None,
        description="MAS endpoint for STATIC constrained decoding requests.",
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
        "extra": "ignore",  # Allow extra env vars like ncbi_api_key, chemspider_api_key
    }


settings = Settings()
