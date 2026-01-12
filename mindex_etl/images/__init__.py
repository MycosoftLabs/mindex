"""
MINDEX Fungal Image Scraping System

The world's largest searchable fungal image database.

Sources:
- iNaturalist (26M+ observations with photos)
- Wikipedia/Wikimedia Commons
- Flickr (Creative Commons)
- Google Images (careful with licensing)
- Pinterest
- Instagram (API limited)
- Research databases (MyCoPortal, GBIF)
- Mushroom Observer

Categories:
- Field observations (mushrooms in nature)
- Lab/Petri dish cultures
- Microscopy (spores, cells)
- Mycelium growth
- Mold/Mildew
- Yeast cultures

Target: 10M+ unique fungal images with 98%+ species match accuracy
"""

from .config import ImageConfig
from .naming import generate_mindex_id, parse_filename
from .hashing import compute_content_hash, compute_perceptual_hash
from .deduplication import ImageDeduplicator
from .downloader import ImageDownloader
from .matcher import SpeciesMatcher

__all__ = [
    "ImageConfig",
    "generate_mindex_id",
    "parse_filename",
    "compute_content_hash",
    "compute_perceptual_hash",
    "ImageDeduplicator",
    "ImageDownloader",
    "SpeciesMatcher",
]
