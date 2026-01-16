# MINDEX HQ Media Ingestion - Implementation Complete

**Date**: 2026-01-16  
**Status**: ✅ IMPLEMENTED  
**Goal**: HQ fungal images with clean metadata, dedupe, and retrieval paths for training + product UI

---

## Executive Summary

All proposed HQ media ingestion features have been implemented, building on MINDEX's existing infrastructure. The implementation follows the "detect + reuse first; only add missing components" principle.

---

## What Was Already Implemented (Found in Phase 0)

| Component | Status | Location |
|-----------|--------|----------|
| PostgreSQL `media.image` table | ✅ Complete | `migrations/0005_images.sql` |
| SHA-256 deduplication | ✅ Complete | `mindex_etl/images/scraper.py` |
| pHash column | ✅ Exists (not populated) | `migrations/0005_images.sql` |
| Multi-source fetcher | ✅ Complete | `mindex_etl/sources/multi_image.py` |
| Image API with backfill | ✅ Complete | `mindex_api/routers/images.py` |
| Vector embeddings | ✅ Column exists | `migrations/0005_images.sql` |
| License/attribution | ✅ Columns exist | `migrations/0005_images.sql` |

---

## What Was Added (Gaps Filled)

### New Modules Created

| File | Purpose |
|------|---------|
| `mindex_etl/images/derivatives.py` | Derivative generation (thumb/small/medium/large + WebP) |
| `mindex_etl/images/phash.py` | pHash computation + Hamming distance for near-dup detection |
| `mindex_etl/images/quality.py` | Quality scoring (resolution, sharpness, noise, color) |
| `mindex_etl/jobs/hq_media_ingestion.py` | Unified HQ ingestion worker with checkpointing |

### Database Migration

| File | Changes |
|------|---------|
| `migrations/0006_hq_media_enhancements.sql` | Label states, quality columns, training views, Hamming distance function |

### Updated Modules

| File | Changes |
|------|---------|
| `mindex_etl/images/__init__.py` | Exports new modules |
| `mindex_etl/images/config.py` | Added settings alias |
| `mindex_etl/sources/multi_image.py` | Added `hq_url` property for original URL resolution |

---

## Feature Details

### 1. Derivative Generation (`derivatives.py`)

Generates multiple sizes from original:
- **thumb**: 150x150 (square crop)
- **small**: 320px long edge
- **medium**: 640px long edge
- **large**: 1280px long edge
- **WebP variants**: All sizes in WebP format

**Usage**:
```python
from mindex_etl.images import generate_derivatives_for_image

result = await generate_derivatives_for_image("/path/to/original.jpg")
print(result.derivatives)  # {'thumb': '...', 'small': '...', ...}
print(result.webp_derivatives)  # {'thumb': '...', ...}
```

### 2. Perceptual Hashing (`phash.py`)

Computes hashes for deduplication:
- **SHA-256**: Exact deduplication
- **pHash**: Near-duplicate detection (Hamming distance ≤ 6)

**Usage**:
```python
from mindex_etl.images import compute_image_hashes, ImageHasher

result = compute_image_hashes("/path/to/image.jpg")
print(f"SHA-256: {result.sha256}")
print(f"pHash: {result.phash}")

hasher = ImageHasher()
is_dup = hasher.is_near_duplicate(phash1, phash2, threshold=6)
```

### 3. Quality Scoring (`quality.py`)

Computes 0-100 quality score based on:
- **Resolution** (30%): Long edge, 1600px+ for HQ
- **Sharpness** (30%): Laplacian variance
- **Noise** (20%): MAD estimation
- **Color** (20%): Saturation and exposure

**Usage**:
```python
from mindex_etl.images import analyze_image_quality, is_hq_image

result = analyze_image_quality("/path/to/image.jpg")
print(f"Quality: {result.quality_score}/100")
print(f"HQ: {result.is_hq}")

# Quick check
is_hq = is_hq_image("/path/to/image.jpg", min_long_edge=1600)
```

### 4. HQ Ingestion Worker (`hq_media_ingestion.py`)

Idempotent, resumable pipeline:
1. Query taxa missing HQ images
2. Search multiple sources (iNat, Wikipedia, GBIF, etc.)
3. Download best HQ original (1600px+)
4. Compute SHA-256 + pHash
5. Check for duplicates
6. Generate derivatives
7. Compute quality score
8. Upsert to database

**Usage**:
```bash
# Full ingestion
python -m mindex_etl.jobs.hq_media_ingestion --limit 100

# Specific sources
python -m mindex_etl.jobs.hq_media_ingestion --sources inat,gbif

# Preview without downloading
python -m mindex_etl.jobs.hq_media_ingestion --dry-run

# Fresh start (ignore checkpoint)
python -m mindex_etl.jobs.hq_media_ingestion --no-resume
```

### 5. Training Dataset Views

SQL views for ML training data export:

```sql
-- HQ Training: quality ≥ 70, license compliant, 1600px+
SELECT * FROM media.training_hq;

-- All labeled images
SELECT * FROM media.training_general;

-- Human-verified only
SELECT * FROM media.training_verified;
```

### 6. Hamming Distance Function

PostgreSQL function for near-duplicate queries:

```sql
-- Find near-duplicates
SELECT * FROM media.image 
WHERE media.hamming_distance(perceptual_hash, 'abc123...') <= 6;

-- View potential duplicates
SELECT * FROM media.potential_duplicates;
```

### 7. Label States

New enum for verification workflow:
- `source_claimed`: Label from original source
- `model_suggested`: ML model suggested
- `human_verified`: Expert verified
- `disputed`: Label contested

---

## Default Thresholds

| Threshold | Value | Notes |
|-----------|-------|-------|
| Min HQ long edge | 1600px | Reject below unless rare species |
| pHash near-dup distance | ≤ 6 | Hamming distance |
| Quality score HQ cutoff | ≥ 70 | For `training_hq` view |
| Derivative sizes | 150/320/640/1280 | thumb/small/medium/large |
| WebP quality | 85 | Good compression/quality balance |
| JPEG quality | 90 | Original derivative quality |

---

## File Structure

```
MINDEX/mindex/
├── migrations/
│   ├── 0005_images.sql              # Original media schema
│   └── 0006_hq_media_enhancements.sql  # NEW: Training views, label states
├── mindex_api/
│   └── routers/
│       └── images.py                # Existing images API
├── mindex_etl/
│   ├── images/
│   │   ├── __init__.py              # UPDATED: Exports new modules
│   │   ├── config.py                # UPDATED: Added settings alias
│   │   ├── derivatives.py           # NEW: Derivative generation
│   │   ├── naming.py                # Existing naming utils
│   │   ├── phash.py                 # NEW: pHash + near-dup detection
│   │   ├── quality.py               # NEW: Quality scoring
│   │   └── scraper.py               # Existing scraper
│   ├── sources/
│   │   └── multi_image.py           # UPDATED: Added hq_url property
│   └── jobs/
│       ├── hq_media_ingestion.py    # NEW: Unified HQ ingestion worker
│       └── backfill_missing_images.py  # Existing backfill
└── docs/
    ├── HQ_MEDIA_SYSTEM_MAP.md       # NEW: System analysis
    └── HQ_MEDIA_IMPLEMENTATION_COMPLETE.md  # NEW: This document
```

---

## Running the Pipeline

### 1. Apply Migration

```bash
docker exec -i mindex-postgres psql -U mindex -d mindex < migrations/0006_hq_media_enhancements.sql
```

### 2. Run HQ Ingestion

```bash
# Inside mindex-api container or with Python environment
cd C:\Users\admin2\Desktop\MYCOSOFT\CODE\MINDEX\mindex

# Process 100 taxa
python -m mindex_etl.jobs.hq_media_ingestion --limit 100

# Resume from checkpoint
python -m mindex_etl.jobs.hq_media_ingestion --limit 500
```

### 3. Verify Results

```sql
-- Check HQ image count
SELECT COUNT(*) FROM media.image WHERE quality_score >= 70;

-- Check training dataset
SELECT COUNT(*) FROM media.training_hq;

-- Check quality distribution
SELECT * FROM media.quality_distribution;
```

---

## Integration with Existing Systems

### NatureOS Dashboard
- HQ images available via existing `/api/natureos/mindex/images/*` endpoints
- Quality scores visible in image metadata

### CREP Dashboard
- Fungal observation images with quality indicators
- Training dataset export for NLM

### Spore Tracker
- HQ species reference images
- Derivative URLs for fast UI rendering

---

## Next Steps (Optional Enhancements)

1. **Object Storage Migration**: Move from local storage to S3/R2
2. **CDN Integration**: CloudFlare for derivative serving
3. **Embeddings Pipeline**: Compute CLIP embeddings for similarity search
4. **Verification UI**: Admin interface for human verification workflow
5. **Scheduled Sync**: Cron job for continuous HQ image discovery

---

## Summary

| Deliverable | Status |
|-------------|--------|
| System Map + Gap List | ✅ `docs/HQ_MEDIA_SYSTEM_MAP.md` |
| Source resolvers with HQ URLs | ✅ `multi_image.py` updated |
| HQ ingestion worker | ✅ `hq_media_ingestion.py` |
| Derivative generator | ✅ `derivatives.py` |
| Dedupe module (SHA-256 + pHash) | ✅ `phash.py` |
| Quality scoring module | ✅ `quality.py` |
| Training dataset views | ✅ `0006_hq_media_enhancements.sql` |
| CLI + checkpointing | ✅ Built into worker |

**All proposed features implemented without breaking existing functionality.**

---

*Document created: 2026-01-16*  
*Implementation: Additive only, no breaking changes*
