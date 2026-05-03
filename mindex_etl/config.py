from __future__ import annotations

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


def _get_default_db_url() -> str:
    """Build database URL from environment or use defaults."""
    host = os.getenv("MINDEX_DB_HOST", "localhost")
    port = os.getenv("MINDEX_DB_PORT", "5432")
    user = os.getenv("MINDEX_DB_USER", "mindex")
    password = os.getenv("MINDEX_DB_PASSWORD", "mindex")
    name = os.getenv("MINDEX_DB_NAME", "mindex")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"

def _default_local_data_dir() -> str:
    # VM runs Linux; keep Windows defaults for local dev.
    if os.name == "nt":
        return "C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_scrape"
    return "/mnt/nas/mindex/scrapes/work"


def _default_nas_data_dir() -> str:
    if os.name == "nt":
        return "\\\\192.168.1.50\\mindex"
    # Convention: mount NAS under /mnt/nas (adjust if your VM differs).
    return "/mnt/nas/mindex"


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

    # NCBI (GenBank / PubMed) - optional API key to increase throughput and reduce 429s
    ncbi_api_key: Optional[str] = Field(
        default=None,
        description="NCBI E-utilities API key (optional).",
    )

    # Local data storage for scraping (use local first, then NAS)
    local_data_dir: str = Field(
        default_factory=_default_local_data_dir,
        description="Local directory for scraped data downloads"
    )
    nas_data_dir: str = Field(
        default_factory=_default_nas_data_dir,
        description="NAS directory for large data storage"
    )

    # iNaturalist - token MUST come from env (INAT_API_TOKEN) - never hardcode
    inat_base_url: str = "https://api.inaturalist.org/v1"
    inat_api_token: str = Field(
        default="",
        description="iNaturalist API JWT token (set INAT_API_TOKEN env var). Required for higher rate limits.",
    )
    inat_rate_limit: float = 0.3  # Faster with API token (3 req/sec)
    # Domain selector: "all" = all life (taxon_id=1), "fungi" = Fungi only (taxon_id=47170, default)
    inat_domain_mode: str = Field(
        default="all",
        description="iNaturalist root filter: 'all' for all life (taxon_id=1) (default); 'fungi' for fungi-only.",
    )

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
    # Domain selector: "all" = all life, "fungi" = Kingdom Fungi only (default), or list of kingdom keys for future per-kingdom mode
    gbif_domain_mode: str = Field(
        default="all",
        description="GBIF root filter: 'all' for all life (default), 'fungi' for fungi-only. Future: comma-separated kingdom keys.",
    )

    # Index Fungorum
    index_fungorum_base_url: str = "http://www.indexfungorum.org"

    # Species Fungorum
    species_fungorum_base_url: str = "http://www.speciesfungorum.org"

    # MycoPortal (for North American fungi)
    mycoportal_base_url: str = "https://mycoportal.org/api"

    # ChemSpider (RSC Compounds API)
    chemspider_base_url: str = "https://api.rsc.org/compounds/v1"
    chemspider_api_key: Optional[str] = Field(
        default=None,
        description="ChemSpider/RSC API key for compound lookups"
    )
    chemspider_rate_limit: float = 0.6  # ~100 requests/minute
    chemspider_cache_ttl: int = 86400  # 24 hours cache

    # =========================================================================
    # EARTH-SCALE DATA SOURCES
    # =========================================================================

    # --- USGS Earthquake Hazards ---
    usgs_earthquake_api: str = "https://earthquake.usgs.gov/fdsnws/event/1"
    usgs_min_magnitude: float = 2.5

    # --- NASA ---
    nasa_api_key: Optional[str] = Field(
        default=None,
        description="NASA API key (DEMO_KEY works with rate limits). Get from https://api.nasa.gov/",
    )
    nasa_firms_map_key: Optional[str] = Field(
        default=None,
        description="NASA FIRMS MAP_KEY for fire/hotspot data. Get from https://firms.modaps.eosdis.nasa.gov/api/",
    )

    # --- NOAA ---
    noaa_swpc_api: str = "https://services.swpc.noaa.gov"
    noaa_ndbc_api: str = "https://www.ndbc.noaa.gov/data/realtime2"
    noaa_nws_api: str = "https://api.weather.gov"
    noaa_gml_api: str = "https://gml.noaa.gov/webdata"

    # --- Air Quality ---
    openaq_api: str = "https://api.openaq.org/v2"
    airnow_api_key: Optional[str] = Field(
        default=None,
        description="EPA AirNow API key for US air quality data.",
    )

    # --- Aviation (ADS-B) ---
    opensky_api: str = "https://opensky-network.org/api"
    opensky_username: Optional[str] = None
    opensky_password: Optional[str] = None
    adsb_exchange_api_key: Optional[str] = Field(
        default=None,
        description="ADS-B Exchange API key for extended aircraft data.",
    )

    # --- Maritime (AIS) ---
    aishub_api_key: Optional[str] = Field(
        default=None,
        description="AISHub API key for vessel tracking data.",
    )

    # --- Cell Towers / Antennas ---
    opencellid_api_key: Optional[str] = Field(
        default=None,
        description="OpenCellID API key for cell tower locations.",
    )

    # --- WiFi / Bluetooth ---
    wigle_api_name: Optional[str] = Field(
        default=None,
        description="WiGLE API name for WiFi/BT network data.",
    )
    wigle_api_token: Optional[str] = Field(
        default=None,
        description="WiGLE API token.",
    )

    # --- Infrastructure ---
    eia_api_key: Optional[str] = Field(
        default=None,
        description="US Energy Information Administration API key.",
    )

    # --- Satellites ---
    celestrak_api: str = "https://celestrak.org"
    spacetrack_username: Optional[str] = None
    spacetrack_password: Optional[str] = None

    # --- Space Launches ---
    launch_library_api: str = "https://ll.thespacedevs.com/2.2.0"

    # --- Domain mode for all-life ingestion ---
    # "all" = ingest all kingdoms (plants, animals, fungi, etc.)
    # "fungi" = fungi only (legacy default)
    earth_domain_mode: str = Field(
        default="all",
        description="Global domain mode: 'all' for full planetary data, 'fungi' for legacy fungi-only.",
    )

    # --- Ingestion scheduling ---
    earth_sync_interval_minutes: int = Field(
        default=15,
        description="How often to poll real-time feeds (earthquakes, aircraft, vessels, weather).",
    )
    earth_full_sync_interval_hours: int = Field(
        default=24,
        description="How often to run full catalog syncs (satellites, facilities, ports, airports).",
    )

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
