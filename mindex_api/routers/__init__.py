from .health import router as health_router
from .emissions import router as emissions_router
from .taxon import router as taxon_router
from .telemetry import telemetry_router, devices_router
from .observations import router as observations_router
from .ip_assets import router as ip_assets_router
from .mycobrain import mycobrain_router
from .stats import router as stats_router
from .wifisense import router as wifisense_router
from .drone import router as drone_router
from .images import router as images_router
from .knowledge import knowledge_router
from .genetics import router as genetics_router
from .compounds import router as compounds_router
from .unified_search import router as unified_search_router
from .research import router as research_router
from .investigation import router as investigation_router
from .a2a_agent import router as a2a_agent_router
from .grounding import router as grounding_router

from .fci import router as fci_router
from .beta import router as beta_router
from .earth import ingest_alias_router, router as earth_router
from .eagle import router as eagle_router
from .plasticity_router import plasticity_router
from .nlm_router import nlm_router
from .search_answers import router as search_answers_router
from .key_management import router as key_management_router

from .etl import router as etl_router
from .phylogeny import router as phylogeny_router
from .all_life import router as all_life_router
from .genomes import router as genomes_router
from .ledger import router as ledger_router
from .mwave import router as mwave_router
from .live_state import router as live_state_router

try:
    from .rag_retrieve import router as rag_retrieve_router
except Exception:
    rag_retrieve_router = None

try:
    from .maritime import router as maritime_router
except Exception:
    maritime_router = None

try:
    from .taco import router as taco_router
except Exception:
    taco_router = None

try:
    from .fusarium_analytics import router as fusarium_analytics_router
except Exception:
    fusarium_analytics_router = None

try:
    from .fusarium_catalog import router as fusarium_catalog_router
except Exception:
    fusarium_catalog_router = None

try:
    from .mycodao_zone import router as mycodao_zone_router
except Exception:
    mycodao_zone_router = None

__all__ = [
    "health_router",
    "emissions_router",
    "taxon_router",
    "telemetry_router",
    "devices_router",
    "observations_router",
    "ip_assets_router",
    "mycobrain_router",
    "stats_router",
    "wifisense_router",
    "drone_router",
    "images_router",
    "knowledge_router",
    "genetics_router",
    "compounds_router",
    "unified_search_router",
    "rag_retrieve_router",
    "research_router",
    "investigation_router",
    "a2a_agent_router",
    "grounding_router",
    "fci_router",
    "beta_router",
    "earth_router",
    "eagle_router",
    "ingest_alias_router",
    "plasticity_router",
    "nlm_router",
    "search_answers_router",
    "key_management_router",
    "etl_router",
    "phylogeny_router",
    "all_life_router",
    "genomes_router",
    "ledger_router",
    "mwave_router",
    "maritime_router",
    "taco_router",
    "fusarium_analytics_router",
    "fusarium_catalog_router",
    "live_state_router",
    "mycodao_zone_router",
]


