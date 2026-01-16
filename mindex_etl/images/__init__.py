"""
MINDEX Fungal Image Processing System
======================================

The world's largest searchable fungal image database.

Sources:
- iNaturalist (26M+ observations with photos)
- Wikipedia/Wikimedia Commons
- GBIF (Global Biodiversity Information Facility)
- Mushroom Observer
- Flickr (Creative Commons)

Modules:
- scraper: Multi-source image scraping
- derivatives: Generate thumb/small/medium/large + WebP variants
- phash: Perceptual hashing for deduplication
- quality: Image quality scoring for training datasets
- naming: MINDEX ID generation
- config: Configuration settings

Target: 10M+ unique fungal images with 98%+ species match accuracy
"""

from .config import ImageConfig, settings as image_settings
from .naming import generate_mindex_id, parse_filename

# Perceptual hashing and deduplication
from .phash import (
    ImageHasher,
    HashResult,
    compute_image_hashes,
    check_duplicate,
    check_near_duplicate,
    DEFAULT_HAMMING_THRESHOLD,
)

# Quality scoring
from .quality import (
    ImageQualityAnalyzer,
    QualityResult,
    analyze_image_quality,
    is_hq_image,
    MIN_HQ_LONG_EDGE,
)

# Derivative generation
from .derivatives import (
    ImageDerivativeGenerator,
    DerivativeResult,
    generate_derivatives_for_image,
    DERIVATIVE_SIZES,
)

# Main scraper
from .scraper import (
    FungalImageScraper,
    ScrapedImage,
)

__all__ = [
    # Config
    "ImageConfig",
    "image_settings",
    
    # Naming
    "generate_mindex_id",
    "parse_filename",
    
    # Hashing
    "ImageHasher",
    "HashResult",
    "compute_image_hashes",
    "check_duplicate",
    "check_near_duplicate",
    "DEFAULT_HAMMING_THRESHOLD",
    
    # Quality
    "ImageQualityAnalyzer",
    "QualityResult",
    "analyze_image_quality",
    "is_hq_image",
    "MIN_HQ_LONG_EDGE",
    
    # Derivatives
    "ImageDerivativeGenerator",
    "DerivativeResult",
    "generate_derivatives_for_image",
    "DERIVATIVE_SIZES",
    
    # Scraper
    "FungalImageScraper",
    "ScrapedImage",
]
