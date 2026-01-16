"""
MINDEX HQ Media Ingestion Worker
=================================
Idempotent, resumable pipeline for HQ fungal image ingestion.

Features:
- Downloads HQ originals (1600px+ long edge)
- Generates derivatives (thumb/small/medium/large + WebP)
- Computes SHA-256 + pHash for deduplication
- Quality scoring for training dataset classification
- License/provenance tracking
- Checkpoint-based resume support
- Rate limiting per source

Usage:
    python -m mindex_etl.jobs.hq_media_ingestion --limit 100 --sources inat,gbif,wikipedia

CLI Args:
    --limit: Max taxa to process (default: 100)
    --sources: Comma-separated sources (default: all)
    --resume: Resume from checkpoint (default: True)
    --dry-run: Preview without downloading
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mindex_etl.sources.multi_image import MultiSourceImageFetcher, ImageResult
from mindex_etl.images.derivatives import ImageDerivativeGenerator, generate_derivatives_for_image
from mindex_etl.images.phash import ImageHasher, compute_image_hashes
from mindex_etl.images.quality import ImageQualityAnalyzer, analyze_image_quality, MIN_HQ_LONG_EDGE
from mindex_etl.images.config import settings as image_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Paths
CHECKPOINT_FILE = Path("C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_scrape/hq_ingestion_checkpoint.json")
IMAGE_STORAGE_BASE = Path(image_settings.local_image_dir)

# Database
DATABASE_URL = os.getenv("MINDEX_DATABASE_URL", "postgresql+asyncpg://mindex:mindex@localhost:5434/mindex")


@dataclass
class IngestionStats:
    """Statistics for an ingestion run."""
    started_at: str = ""
    taxa_processed: int = 0
    images_found: int = 0
    images_downloaded: int = 0
    images_skipped_duplicate: int = 0
    images_skipped_low_quality: int = 0
    derivatives_generated: int = 0
    errors: int = 0
    completed_at: Optional[str] = None


@dataclass
class Checkpoint:
    """Checkpoint for resumable ingestion."""
    processed_taxon_ids: Set[str] = field(default_factory=set)
    last_taxon_id: Optional[str] = None
    stats: IngestionStats = field(default_factory=IngestionStats)
    last_update: str = ""
    
    def save(self, path: Path = CHECKPOINT_FILE):
        """Save checkpoint to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "processed_taxon_ids": list(self.processed_taxon_ids),
            "last_taxon_id": self.last_taxon_id,
            "stats": {
                "started_at": self.stats.started_at,
                "taxa_processed": self.stats.taxa_processed,
                "images_found": self.stats.images_found,
                "images_downloaded": self.stats.images_downloaded,
                "images_skipped_duplicate": self.stats.images_skipped_duplicate,
                "images_skipped_low_quality": self.stats.images_skipped_low_quality,
                "derivatives_generated": self.stats.derivatives_generated,
                "errors": self.stats.errors,
            },
            "last_update": datetime.now().isoformat(),
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.debug(f"Checkpoint saved: {self.stats.taxa_processed} taxa processed")
    
    @classmethod
    def load(cls, path: Path = CHECKPOINT_FILE) -> "Checkpoint":
        """Load checkpoint from disk."""
        if not path.exists():
            return cls()
        
        try:
            with open(path, "r") as f:
                data = json.load(f)
            
            checkpoint = cls()
            checkpoint.processed_taxon_ids = set(data.get("processed_taxon_ids", []))
            checkpoint.last_taxon_id = data.get("last_taxon_id")
            checkpoint.last_update = data.get("last_update", "")
            
            stats_data = data.get("stats", {})
            checkpoint.stats = IngestionStats(
                started_at=stats_data.get("started_at", ""),
                taxa_processed=stats_data.get("taxa_processed", 0),
                images_found=stats_data.get("images_found", 0),
                images_downloaded=stats_data.get("images_downloaded", 0),
                images_skipped_duplicate=stats_data.get("images_skipped_duplicate", 0),
                images_skipped_low_quality=stats_data.get("images_skipped_low_quality", 0),
                derivatives_generated=stats_data.get("derivatives_generated", 0),
                errors=stats_data.get("errors", 0),
            )
            
            logger.info(f"Loaded checkpoint: {len(checkpoint.processed_taxon_ids)} taxa already processed")
            return checkpoint
            
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}")
            return cls()


class HQMediaIngestionWorker:
    """
    Worker for ingesting HQ fungal media.
    
    Pipeline steps:
    1. Query taxa missing HQ images
    2. For each taxon, search multiple sources
    3. Download best HQ original (1600px+ long edge)
    4. Validate and compute hashes
    5. Check for duplicates
    6. Generate derivatives
    7. Compute quality score
    8. Upsert to database
    """
    
    def __init__(
        self,
        limit: int = 100,
        sources: Optional[List[str]] = None,
        resume: bool = True,
        dry_run: bool = False,
    ):
        self.limit = limit
        self.sources = sources or ["inat", "wikipedia", "mushroom_observer", "gbif", "flickr"]
        self.resume = resume
        self.dry_run = dry_run
        
        self.checkpoint = Checkpoint.load() if resume else Checkpoint()
        self.hasher = ImageHasher()
        self.quality_analyzer = ImageQualityAnalyzer()
        self.derivative_generator = ImageDerivativeGenerator(
            output_base=IMAGE_STORAGE_BASE / "derivatives"
        )
        
        # Track existing hashes for deduplication
        self.existing_sha256: Set[str] = set()
        self.existing_phash: List[tuple] = []
        
        # HTTP client
        self.http_client: Optional[httpx.AsyncClient] = None
    
    async def _init_db(self) -> AsyncSession:
        """Initialize database connection."""
        engine = create_async_engine(DATABASE_URL, echo=False)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        return async_session()
    
    async def _load_existing_hashes(self, db: AsyncSession):
        """Load existing hashes for deduplication."""
        logger.info("Loading existing hashes for deduplication...")
        
        result = await db.execute(text("""
            SELECT content_hash, perceptual_hash, id 
            FROM media.image 
            WHERE content_hash IS NOT NULL
        """))
        
        for row in result.mappings():
            if row["content_hash"]:
                self.existing_sha256.add(row["content_hash"])
            if row["perceptual_hash"]:
                self.existing_phash.append((str(row["id"]), row["perceptual_hash"]))
        
        logger.info(f"Loaded {len(self.existing_sha256)} existing SHA-256 hashes")
    
    async def _get_taxa_missing_hq_images(self, db: AsyncSession) -> List[Dict[str, Any]]:
        """Get taxa that need HQ images."""
        logger.info(f"Querying taxa missing HQ images (limit: {self.limit})...")
        
        # Exclude already processed taxa
        processed_ids = list(self.checkpoint.processed_taxon_ids)
        
        query = """
            SELECT 
                t.id,
                t.canonical_name,
                t.source,
                (t.metadata->>'observations_count')::int as observations_count
            FROM core.taxon t
            LEFT JOIN media.image i ON t.id = i.taxon_id AND i.quality_score >= 70
            WHERE i.id IS NULL
              AND t.id NOT IN (SELECT UNNEST(:processed_ids::uuid[]))
            ORDER BY 
                CASE 
                    WHEN (t.metadata->>'observations_count') ~ '^[0-9]+$' 
                    THEN (t.metadata->>'observations_count')::bigint 
                    ELSE 0 
                END DESC
            LIMIT :limit
        """
        
        result = await db.execute(text(query), {
            "processed_ids": processed_ids or ['00000000-0000-0000-0000-000000000000'],
            "limit": self.limit,
        })
        
        taxa = [dict(row) for row in result.mappings()]
        logger.info(f"Found {len(taxa)} taxa needing HQ images")
        return taxa
    
    async def _download_image(self, url: str, save_path: Path) -> Optional[bytes]:
        """Download image from URL."""
        try:
            if not self.http_client:
                self.http_client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
            
            response = await self.http_client.get(url, headers={
                "User-Agent": "MINDEX-HQ-Ingestion/1.0 (Mycosoft Fungal Database)"
            })
            
            if response.status_code != 200:
                logger.warning(f"Download failed ({response.status_code}): {url}")
                return None
            
            content = response.content
            
            # Validate content type
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                logger.warning(f"Not an image ({content_type}): {url}")
                return None
            
            # Save to disk
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(content)
            
            return content
            
        except Exception as e:
            logger.error(f"Download error: {e}")
            return None
    
    async def _process_taxon(
        self,
        db: AsyncSession,
        taxon: Dict[str, Any],
        fetcher: MultiSourceImageFetcher,
    ) -> bool:
        """Process a single taxon to find and ingest HQ image."""
        taxon_id = str(taxon["id"])
        canonical_name = taxon["canonical_name"]
        
        logger.info(f"Processing: {canonical_name}")
        
        try:
            # Search for images
            images = await fetcher.find_all_images(canonical_name, self.sources)
            
            if not images:
                logger.debug(f"No images found for {canonical_name}")
                return False
            
            self.checkpoint.stats.images_found += len(images)
            
            # Try each image until we find a good one
            for img in images:
                # Prefer original URL
                url = img.original_url or img.url
                
                if self.dry_run:
                    logger.info(f"  [DRY-RUN] Would download: {url}")
                    continue
                
                # Generate storage path
                species_safe = canonical_name.replace(" ", "_").replace("/", "-")[:50]
                first_letter = species_safe[0].upper() if species_safe else "X"
                mindex_id = f"MYCO-IMG-{taxon_id[:8].upper()}"
                
                save_dir = IMAGE_STORAGE_BASE / "originals" / first_letter / species_safe
                save_path = save_dir / f"{img.source}_{mindex_id}.jpg"
                
                # Download
                content = await self._download_image(url, save_path)
                if not content:
                    continue
                
                # Compute hashes
                hash_result = compute_image_hashes(str(save_path))
                
                if not hash_result.success:
                    save_path.unlink(missing_ok=True)
                    continue
                
                # Check for exact duplicate
                if hash_result.sha256 in self.existing_sha256:
                    logger.debug(f"  Skipping exact duplicate: {canonical_name}")
                    save_path.unlink(missing_ok=True)
                    self.checkpoint.stats.images_skipped_duplicate += 1
                    continue
                
                # Check for near-duplicate
                if hash_result.phash:
                    near_dup = self.hasher.find_near_duplicates(
                        hash_result.phash,
                        self.existing_phash,
                        threshold=6
                    )
                    if near_dup:
                        logger.debug(f"  Near-duplicate found (distance {near_dup[0][1]})")
                        # Keep both but mark as related
                
                # Analyze quality
                quality_result = analyze_image_quality(str(save_path))
                
                if not quality_result.success:
                    save_path.unlink(missing_ok=True)
                    continue
                
                # Check HQ threshold
                if not quality_result.meets_min_resolution:
                    logger.debug(f"  Below HQ threshold ({quality_result.long_edge}px)")
                    save_path.unlink(missing_ok=True)
                    self.checkpoint.stats.images_skipped_low_quality += 1
                    continue
                
                # Generate derivatives
                deriv_result = await generate_derivatives_for_image(
                    str(save_path),
                    str(IMAGE_STORAGE_BASE / "derivatives" / first_letter / species_safe)
                )
                
                if deriv_result.success:
                    self.checkpoint.stats.derivatives_generated += 1
                
                # Determine license compliance
                license_compliant = self._is_license_compliant(img.license)
                
                # Insert into database
                await db.execute(text("""
                    INSERT INTO media.image (
                        mindex_id, filename, file_path, file_size_bytes,
                        width, height, format, content_hash, perceptual_hash,
                        source, source_url, license, attribution,
                        taxon_id, species_confidence, species_match_method,
                        quality_score, resolution_score, sharpness_score,
                        noise_score, color_score, derivatives,
                        license_compliant, label_state, scraped_at
                    ) VALUES (
                        :mindex_id, :filename, :file_path, :file_size,
                        :width, :height, :format, :sha256, :phash,
                        :source, :source_url, :license, :attribution,
                        cast(:taxon_id as uuid), :confidence, :match_method,
                        :quality_score, :resolution_score, :sharpness_score,
                        :noise_score, :color_score, cast(:derivatives as jsonb),
                        :license_compliant, 'source_claimed', NOW()
                    )
                    ON CONFLICT (content_hash) DO NOTHING
                """), {
                    "mindex_id": mindex_id,
                    "filename": save_path.name,
                    "file_path": str(save_path),
                    "file_size": len(content),
                    "width": quality_result.width,
                    "height": quality_result.height,
                    "format": "jpg",
                    "sha256": hash_result.sha256,
                    "phash": hash_result.phash,
                    "source": img.source,
                    "source_url": img.source_url or url,
                    "license": img.license,
                    "attribution": img.photographer,
                    "taxon_id": taxon_id,
                    "confidence": img.quality_score / 100.0,
                    "match_method": "api",
                    "quality_score": quality_result.quality_score,
                    "resolution_score": quality_result.resolution_score,
                    "sharpness_score": quality_result.sharpness_score,
                    "noise_score": quality_result.noise_score,
                    "color_score": quality_result.color_score,
                    "derivatives": json.dumps({
                        **deriv_result.derivatives,
                        "webp": deriv_result.webp_derivatives,
                    }),
                    "license_compliant": license_compliant,
                })
                
                await db.commit()
                
                # Update tracking
                self.existing_sha256.add(hash_result.sha256)
                if hash_result.phash:
                    self.existing_phash.append((taxon_id, hash_result.phash))
                
                self.checkpoint.stats.images_downloaded += 1
                logger.info(f"  ✓ Downloaded HQ image ({quality_result.quality_score:.1f} quality)")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error processing {canonical_name}: {e}")
            self.checkpoint.stats.errors += 1
            return False
    
    def _is_license_compliant(self, license_str: Optional[str]) -> bool:
        """Check if license is acceptable for training."""
        if not license_str:
            return True  # Assume OK if unknown
        
        license_lower = license_str.lower()
        
        # Acceptable licenses
        acceptable = ["cc0", "cc-by", "cc-by-sa", "public", "pd", "cc-by-nc"]
        for acc in acceptable:
            if acc in license_lower:
                return True
        
        # Unacceptable licenses
        unacceptable = ["all-rights", "copyright", "nd"]  # No derivatives
        for unacc in unacceptable:
            if unacc in license_lower:
                return False
        
        return True  # Default to OK
    
    async def run(self):
        """Run the ingestion pipeline."""
        logger.info("=" * 60)
        logger.info("MINDEX HQ Media Ingestion Worker")
        logger.info("=" * 60)
        logger.info(f"Limit: {self.limit}")
        logger.info(f"Sources: {', '.join(self.sources)}")
        logger.info(f"Resume: {self.resume}")
        logger.info(f"Dry Run: {self.dry_run}")
        logger.info("")
        
        self.checkpoint.stats.started_at = datetime.now().isoformat()
        
        try:
            db = await self._init_db()
            
            # Load existing hashes
            await self._load_existing_hashes(db)
            
            # Get taxa to process
            taxa = await self._get_taxa_missing_hq_images(db)
            
            if not taxa:
                logger.info("No taxa need HQ images!")
                return
            
            # Process each taxon
            async with MultiSourceImageFetcher() as fetcher:
                for taxon in taxa:
                    taxon_id = str(taxon["id"])
                    
                    # Skip if already processed
                    if taxon_id in self.checkpoint.processed_taxon_ids:
                        continue
                    
                    success = await self._process_taxon(db, taxon, fetcher)
                    
                    # Update checkpoint
                    self.checkpoint.processed_taxon_ids.add(taxon_id)
                    self.checkpoint.last_taxon_id = taxon_id
                    self.checkpoint.stats.taxa_processed += 1
                    
                    # Save checkpoint every 10 taxa
                    if self.checkpoint.stats.taxa_processed % 10 == 0:
                        self.checkpoint.save()
                    
                    # Rate limit
                    await asyncio.sleep(0.5)
            
            self.checkpoint.stats.completed_at = datetime.now().isoformat()
            self.checkpoint.save()
            
            # Summary
            logger.info("")
            logger.info("=" * 60)
            logger.info("INGESTION COMPLETE")
            logger.info("=" * 60)
            logger.info(f"Taxa processed: {self.checkpoint.stats.taxa_processed}")
            logger.info(f"Images found: {self.checkpoint.stats.images_found}")
            logger.info(f"Images downloaded: {self.checkpoint.stats.images_downloaded}")
            logger.info(f"Duplicates skipped: {self.checkpoint.stats.images_skipped_duplicate}")
            logger.info(f"Low quality skipped: {self.checkpoint.stats.images_skipped_low_quality}")
            logger.info(f"Derivatives generated: {self.checkpoint.stats.derivatives_generated}")
            logger.info(f"Errors: {self.checkpoint.stats.errors}")
            
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            self.checkpoint.save()
            raise
        
        finally:
            if self.http_client:
                await self.http_client.aclose()


async def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="MINDEX HQ Media Ingestion Worker")
    parser.add_argument("--limit", type=int, default=100, help="Max taxa to process")
    parser.add_argument("--sources", type=str, default=None, help="Comma-separated sources")
    parser.add_argument("--no-resume", action="store_true", help="Start fresh (ignore checkpoint)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without downloading")
    
    args = parser.parse_args()
    
    sources = args.sources.split(",") if args.sources else None
    
    worker = HQMediaIngestionWorker(
        limit=args.limit,
        sources=sources,
        resume=not args.no_resume,
        dry_run=args.dry_run,
    )
    
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
