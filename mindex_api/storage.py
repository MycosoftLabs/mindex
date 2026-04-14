"""
Tiered Storage Manager
========================
Manages data across three storage tiers:

Tier 1 — HOT: PostgreSQL (local SSD)
    - <5ms latency
    - Recent data, frequently accessed entities
    - Search indexes, PostGIS spatial queries
    - Auto-evicts old data to warm/cold tiers

Tier 2 — WARM: Supabase (cloud)
    - <50ms latency (global CDN)
    - All data synced from local DB
    - PostgREST API for CREP web, agents, MCP
    - Realtime subscriptions for live updates
    - Storage buckets for images, files

Tier 3 — COLD: NAS (Ubiquiti, local network)
    - <10ms LAN latency for bulk reads
    - 16TB base + 6 bays (27TB drives) = ~178TB max
    - Raw scrape archives, training data exports
    - Historical time-series (telemetry, weather, ADS-B)
    - Image archives, TLE archives, genome files

Data Flow:
    External APIs → PostgreSQL (hot) → Supabase (warm) → NAS (cold)

    Writes: Always local PostgreSQL first, then async fan-out
    Reads: Cache → PostgreSQL → Supabase → NAS → Live scrape
    Archive: Scheduled job moves old hot data to NAS, updates Supabase
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import settings

logger = logging.getLogger(__name__)


class StorageManager:
    """Manages data across hot/warm/cold storage tiers."""

    def __init__(self):
        # NAS mount paths
        self.nas_base = getattr(settings, "nas_mount_path", "/mnt/nas/mindex")
        self.nas_archive = Path(self.nas_base) / "archive"
        self.nas_training = Path(self.nas_base) / "training"
        self.nas_scrapes = Path(self.nas_base) / "scrapes"
        self.nas_images = Path(self.nas_base) / "images"
        self.nas_telemetry = Path(self.nas_base) / "telemetry"

        # Local scratch for staging
        self.local_staging = Path(
            getattr(settings, "local_staging_path", "/tmp/mindex_staging")
        )

    def ensure_dirs(self):
        """Create NAS directory structure if NAS is mounted."""
        if not Path(self.nas_base).exists():
            logger.warning(f"NAS not mounted at {self.nas_base}")
            return False

        for d in [
            self.nas_archive, self.nas_training, self.nas_scrapes,
            self.nas_images, self.nas_telemetry,
            self.nas_archive / "earthquakes",
            self.nas_archive / "weather",
            self.nas_archive / "aircraft",
            self.nas_archive / "vessels",
            self.nas_archive / "telemetry",
            self.nas_archive / "species",
            self.nas_training / "nlm",
            self.nas_training / "embeddings",
            self.nas_training / "datasets",
            self.nas_training / "fusarium",
            self.nas_training / "fusarium" / "registry",
            self.nas_training / "fusarium" / "underwater_pam",
            self.nas_training / "fusarium" / "vessel_uatr",
            self.nas_training / "fusarium" / "marine_bio",
            self.nas_training / "fusarium" / "aerial_bio_uav",
            self.nas_training / "fusarium" / "threat_munitions",
            self.nas_training / "fusarium" / "env_transfer_audio",
            self.nas_training / "fusarium" / "oceanographic_grid",
            self.nas_training / "fusarium" / "bathymetry",
            self.nas_training / "fusarium" / "magnetic",
            self.nas_training / "fusarium" / "ais_maritime",
            self.nas_training / "fusarium" / "sonar_imagery",
            self.nas_training / "fusarium" / "gas_chemistry",
            self.nas_training / "fusarium" / "electromagnetics",
            self.nas_training / "fusarium" / "vibration_touch",
            self.nas_training / "fusarium" / "bioelectric_fci",
            self.nas_training / "fusarium" / "model_registry",
            self.nas_scrapes / "raw",
            self.nas_scrapes / "processed",
            self.nas_images / "species",
            self.nas_images / "satellite",
            self.nas_images / "webcam",
        ]:
            d.mkdir(parents=True, exist_ok=True)

        self.local_staging.mkdir(parents=True, exist_ok=True)
        logger.info(f"NAS directory structure ready at {self.nas_base}")
        return True

    # =========================================================================
    # NAS FILE OPERATIONS
    # =========================================================================

    def write_to_nas(self, category: str, filename: str, data: bytes) -> Optional[str]:
        """Write raw data to NAS cold storage."""
        target = Path(self.nas_base) / category / filename
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            logger.debug(f"NAS write: {target} ({len(data)} bytes)")
            return str(target)
        except Exception as e:
            logger.error(f"NAS write error: {e}")
            return None

    def write_json_to_nas(self, category: str, filename: str, data: Any) -> Optional[str]:
        """Write JSON data to NAS."""
        raw = json.dumps(data, default=str, indent=2).encode("utf-8")
        return self.write_to_nas(category, filename, raw)

    def read_from_nas(self, category: str, filename: str) -> Optional[bytes]:
        """Read raw data from NAS."""
        target = Path(self.nas_base) / category / filename
        try:
            return target.read_bytes() if target.exists() else None
        except Exception as e:
            logger.error(f"NAS read error: {e}")
            return None

    def read_json_from_nas(self, category: str, filename: str) -> Optional[Any]:
        """Read JSON from NAS."""
        data = self.read_from_nas(category, filename)
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                pass
        return None

    def nas_available(self) -> bool:
        """Check if NAS is mounted and writable."""
        try:
            return Path(self.nas_base).exists() and os.access(self.nas_base, os.W_OK)
        except Exception:
            return False

    def nas_usage(self) -> Dict[str, Any]:
        """Get NAS disk usage info."""
        if not self.nas_available():
            return {"available": False}

        try:
            usage = shutil.disk_usage(self.nas_base)
            return {
                "available": True,
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "usage_pct": round(usage.used / usage.total * 100, 1),
                "mount_path": self.nas_base,
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    # =========================================================================
    # ARCHIVE OPERATIONS — Move old hot data to NAS
    # =========================================================================

    def archive_domain_data(
        self, domain: str, data: List[dict], date_str: Optional[str] = None,
    ) -> Optional[str]:
        """Archive domain data to NAS as compressed JSON."""
        if not self.nas_available():
            return None

        date_str = date_str or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{domain}_{date_str}.json"
        return self.write_json_to_nas(f"archive/{domain}", filename, data)

    # =========================================================================
    # TRAINING DATA EXPORT — NLM Training Pipeline
    # =========================================================================

    def export_training_data(
        self, dataset_name: str, records: List[dict],
        format: str = "jsonl",
    ) -> Optional[str]:
        """Export data to NAS for Nature Learning Model training.

        Supports JSONL (line-delimited JSON) for streaming training.
        """
        if not self.nas_available():
            return None

        filename = f"{dataset_name}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.{format}"
        target = self.nas_training / "nlm" / filename

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                if format == "jsonl":
                    for record in records:
                        f.write(json.dumps(record, default=str) + "\n")
                else:
                    json.dump(records, f, default=str, indent=2)

            logger.info(f"Training export: {target} ({len(records)} records)")
            return str(target)
        except Exception as e:
            logger.error(f"Training export error: {e}")
            return None

    def export_fusarium_training_data(
        self,
        modality_silo: str,
        dataset_name: str,
        records: List[dict],
        format: str = "jsonl",
    ) -> Optional[str]:
        """Export defense-compartmented training data for Fusarium/NLM workflows."""
        if not self.nas_available():
            return None

        filename = f"{dataset_name}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.{format}"
        target = self.nas_training / "fusarium" / modality_silo / filename

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                if format == "jsonl":
                    for record in records:
                        f.write(json.dumps(record, default=str) + "\n")
                else:
                    json.dump(records, f, default=str, indent=2)

            logger.info("Fusarium training export: %s (%s records)", target, len(records))
            return str(target)
        except Exception as e:
            logger.error(f"Fusarium training export error: {e}")
            return None

    # =========================================================================
    # IMAGE STORAGE — Species images, satellite imagery, webcam snapshots
    # =========================================================================

    def store_image(
        self, category: str, filename: str, data: bytes,
    ) -> Optional[str]:
        """Store image on NAS."""
        return self.write_to_nas(f"images/{category}", filename, data)

    def get_image_path(self, category: str, filename: str) -> Optional[str]:
        """Get NAS path for an image."""
        target = Path(self.nas_base) / "images" / category / filename
        return str(target) if target.exists() else None


# =========================================================================
# SINGLETON
# =========================================================================

_storage: Optional[StorageManager] = None


def get_storage() -> StorageManager:
    """Get singleton storage manager."""
    global _storage
    if _storage is None:
        _storage = StorageManager()
    return _storage
