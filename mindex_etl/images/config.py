"""
Image Scraping Configuration

Storage paths and API configurations for image scraping.
"""

from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class ImageConfig(BaseSettings):
    """Configuration for the fungal image scraping system."""
    
    # Local Storage Paths
    local_image_dir: str = Field(
        default="C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_images",
        description="Primary local storage for images (12TB available)"
    )
    nas_image_dir: str = Field(
        default="\\\\192.168.1.50\\mindex_images",
        description="NAS backup storage for images (16TB)"
    )
    dream_machine_dir: str = Field(
        default="\\\\192.168.1.1\\mindex_backup\\images",
        description="Dream Machine backup storage (27TB)"
    )
    
    # Subdirectories by type
    field_subdir: str = "field"           # Field observations
    lab_subdir: str = "lab"               # Lab/petri dish
    microscope_subdir: str = "microscope" # Microscopy images
    mycelium_subdir: str = "mycelium"     # Mycelium growth
    mold_subdir: str = "mold"             # Mold/mildew
    yeast_subdir: str = "yeast"           # Yeast cultures
    spore_subdir: str = "spores"          # Spore prints/images
    
    # iNaturalist Config
    inat_base_url: str = "https://api.inaturalist.org/v1"
    inat_api_token: str = Field(
        default="eyJhbGciOiJIUzUxMiJ9.eyJ1c2VyX2lkIjoxMDAxOTc2OSwiZXhwIjoxNzY1OTE1MDY2fQ.JXV3lLOyuuXeItfNUagixJCtKN3SI20_em1sl2gKFFDppHBNJXy79x6I6jJbiPG1a6n_-cj1JgysSmuKlbDKVg",
        description="iNaturalist API token"
    )
    inat_fungi_taxon_id: int = 47170  # Fungi kingdom
    
    # Wikimedia Commons
    wikimedia_api: str = "https://commons.wikimedia.org/w/api.php"
    
    # Flickr
    flickr_api_key: Optional[str] = None
    flickr_api_secret: Optional[str] = None
    
    # Mushroom Observer
    mushroom_observer_api: str = "https://mushroomobserver.org/api2"
    
    # GBIF
    gbif_api: str = "https://api.gbif.org/v1"
    
    # Image Processing
    max_image_size_mb: int = 50
    target_image_formats: list = ["jpg", "jpeg", "png", "webp"]
    thumbnail_sizes: list = [(128, 128), (256, 256), (512, 512)]
    
    # Deduplication
    similarity_threshold: float = 0.95  # pHash similarity threshold
    exact_match_skip: bool = True       # Skip exact content hash matches
    
    # Species Matching
    min_confidence_threshold: float = 0.80  # Minimum to store
    high_confidence_threshold: float = 0.98  # Target accuracy
    
    # Rate Limiting
    requests_per_second: float = 2.0
    max_concurrent_downloads: int = 10
    
    # Naming Convention
    # Format: {source}_{species}_{date}_{mindex_id}.{ext}
    # Example: inat_Amanita_muscaria_20260110_MYCO-IMG-00000001.jpg
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }
    
    def get_storage_path(self, image_type: str = "field") -> Path:
        """Get the storage path for a specific image type."""
        base = Path(self.local_image_dir)
        type_map = {
            "field": self.field_subdir,
            "lab": self.lab_subdir,
            "petri": self.lab_subdir,
            "microscope": self.microscope_subdir,
            "mycelium": self.mycelium_subdir,
            "mold": self.mold_subdir,
            "mildew": self.mold_subdir,
            "yeast": self.yeast_subdir,
            "spore": self.spore_subdir,
        }
        subdir = type_map.get(image_type.lower(), "other")
        path = base / subdir
        path.mkdir(parents=True, exist_ok=True)
        return path


# Global config instance
config = ImageConfig()
