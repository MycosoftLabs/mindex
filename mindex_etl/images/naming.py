"""
Image Naming and ID System

Generates unique, searchable filenames for fungal images.

Naming Convention:
    {source}_{species_safe}_{date}_{mindex_id}.{ext}
    
Examples:
    inat_Amanita_muscaria_20260110_MYCO-IMG-00000001.jpg
    wiki_Psilocybe_cubensis_20251231_MYCO-IMG-00000002.png
    flickr_Lactarius_deliciosus_20260105_MYCO-IMG-00000003.jpg
"""

import re
import uuid
from datetime import datetime, date
from typing import Optional, Tuple
from pathlib import Path


# Global counter for MINDEX IDs (in production, use database sequence)
_mindex_counter = 0


def sanitize_species_name(name: str) -> str:
    """
    Convert species name to safe filename format.
    
    Examples:
        "Amanita muscaria" -> "Amanita_muscaria"
        "Ganoderma sp." -> "Ganoderma_sp"
        "Pleurotus ostreatus (oyster)" -> "Pleurotus_ostreatus"
    """
    if not name:
        return "Unknown"
    
    # Remove parenthetical notes
    name = re.sub(r'\([^)]*\)', '', name)
    
    # Remove special characters except spaces and underscores
    name = re.sub(r'[^\w\s-]', '', name)
    
    # Replace spaces with underscores
    name = name.strip().replace(' ', '_')
    
    # Remove consecutive underscores
    name = re.sub(r'_+', '_', name)
    
    # Limit length
    return name[:100] if len(name) > 100 else name


def generate_mindex_id(prefix: str = "MYCO-IMG") -> str:
    """
    Generate a unique MINDEX image ID.
    
    Format: MYCO-IMG-XXXXXXXX (8 digit sequential number)
    
    In production, this should use a database sequence for uniqueness.
    """
    global _mindex_counter
    _mindex_counter += 1
    
    # In production, get this from database sequence
    # For now, use UUID-based unique suffix
    unique_suffix = uuid.uuid4().hex[:8].upper()
    
    return f"{prefix}-{unique_suffix}"


def generate_filename(
    source: str,
    species_name: Optional[str] = None,
    capture_date: Optional[date] = None,
    mindex_id: Optional[str] = None,
    extension: str = "jpg"
) -> str:
    """
    Generate a standardized filename for a fungal image.
    
    Args:
        source: Image source (inat, wiki, flickr, etc.)
        species_name: Scientific name of the species
        capture_date: Date the image was captured
        mindex_id: MINDEX ID (generated if not provided)
        extension: File extension
    
    Returns:
        Formatted filename
        
    Example:
        generate_filename("inat", "Amanita muscaria", date(2026, 1, 10))
        -> "inat_Amanita_muscaria_20260110_MYCO-IMG-A1B2C3D4.jpg"
    """
    # Source prefix
    source_prefix = source.lower()[:10]
    
    # Species name
    species_safe = sanitize_species_name(species_name) if species_name else "Unknown"
    
    # Date
    date_str = capture_date.strftime("%Y%m%d") if capture_date else datetime.now().strftime("%Y%m%d")
    
    # MINDEX ID
    mid = mindex_id or generate_mindex_id()
    
    # Extension
    ext = extension.lower().lstrip('.')
    
    return f"{source_prefix}_{species_safe}_{date_str}_{mid}.{ext}"


def parse_filename(filename: str) -> dict:
    """
    Parse a MINDEX image filename to extract metadata.
    
    Args:
        filename: The filename to parse
        
    Returns:
        Dictionary with source, species, date, mindex_id, extension
    """
    # Remove path if present
    name = Path(filename).stem
    ext = Path(filename).suffix.lstrip('.')
    
    # Pattern: source_species_date_mindex-id
    pattern = r'^(\w+)_(.+)_(\d{8})_(MYCO-IMG-[A-Z0-9]+)$'
    match = re.match(pattern, name)
    
    if match:
        return {
            "source": match.group(1),
            "species_name": match.group(2).replace('_', ' '),
            "date": datetime.strptime(match.group(3), "%Y%m%d").date(),
            "mindex_id": match.group(4),
            "extension": ext,
            "parsed": True,
        }
    
    return {
        "source": None,
        "species_name": None,
        "date": None,
        "mindex_id": None,
        "extension": ext,
        "parsed": False,
        "original": name,
    }


def get_storage_path(
    base_dir: str,
    source: str,
    species_name: Optional[str] = None,
    image_type: str = "field"
) -> Path:
    """
    Generate the full storage path for an image.
    
    Structure:
        base_dir/
            field/
                A/  (first letter of genus)
                    Amanita/
                        inat/