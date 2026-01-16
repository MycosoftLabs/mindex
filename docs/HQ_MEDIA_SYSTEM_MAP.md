# MINDEX HQ Media System - System Map & Gap Analysis

**Date**: 2026-01-16  
**Status**: Analysis Complete  
**Purpose**: Identify existing components and gaps for HQ fungal media ingestion

---

## Executive Summary

MINDEX already has **significant media infrastructure** in place. The proposal's goals can be achieved by **enhancing existing components** rather than building from scratch.

**Key Finding**: ~70% of the proposed features already exist. Focus on filling gaps.

---

## System Map: Existing Components

### 1. Database Schema ✅ COMPLETE

**Location**: `migrations/0005_images.sql`

```sql
-- media.image table with:
- id (UUID)
- mindex_id (MYCO-IMG-XXXXXXXX format) ✅
- content_hash (SHA-256) ✅
- perceptual_hash (pHash column exists) ⚠️ (not populated)
- source, source_url, source_id ✅
- license, attribution ✅
- file_path, file_size_bytes, width, height, format ✅
- capture_date, capture_location (PostGIS) ✅
- taxon_id (FK to core.taxon) ✅
- species_confidence, verified ✅
- image_type, subject_type, environment, growth_stage ✅
- embedding (VECTOR 512) ✅
- ML features: color_histogram, detected_features ✅
```

**Collections**: `media.image_collection`, `media.image_collection_item` ✅
**Similarity**: `media.image_similarity` ✅
**Job Tracking**: `media.scrape_job` ✅
**Statistics Views**: `media.image_stats`, `media.image_by_source`, `media.image_by_type` ✅

### 2. Multi-Source Image Fetcher ✅ COMPLETE

**Location**: `mindex_etl/sources/multi_image.py`

| Source | Status | Priority | Notes |
|--------|--------|----------|-------|
| iNaturalist | ✅ | 100 | Gets default_photo + observation photos |
| Wikipedia | ✅ | 85 | Gets thumbnail + original |
| Wikimedia Commons | ✅ | 85 | Searches Commons directly |
| Mushroom Observer | ✅ | 90 | Gets orig/640/thumb sizes |
| GBIF | ✅ | 60 | Occurrence media |
| Flickr | ✅ | 70 | Creative Commons feed |
| Bing | ✅ | 40 | Web scraping fallback |

**Features**:
- Rate limiting per source ✅
- Quality scoring (basic) ✅
- Priority-based sorting ✅
- Async parallel fetching ✅

### 3. Image API ✅ COMPLETE

**Location**: `mindex_api/routers/images.py`

| Endpoint | Status | Description |
|----------|--------|-------------|
| `GET /images/stats` | ✅ | Coverage statistics |
| `GET /images/missing` | ✅ | Taxa without images |
| `POST /images/backfill/start` | ✅ | Start backfill job |
| `GET /images/backfill/status` | ✅ | Job status |
| `GET /images/search/{species}` | ✅ | Live search |
| `POST /images/backfill/single/{id}` | ✅ | Single taxon backfill |

### 4. Image Scraper ✅ PARTIAL

**Location**: `mindex_etl/images/scraper.py`

**Existing**:
- Download to local filesystem ✅
- SHA-256 deduplication ✅
- Quality scoring (basic) ✅
- Species-organized folder structure ✅

**Missing**: See Gap List below

### 5. ETL Jobs ✅ PARTIAL

**Location**: `mindex_etl/jobs/`

| Job | Status |
|-----|--------|
| `backfill_missing_images.py` | ✅ |
| `backfill_inat_taxon_photos.py` | ✅ |
| `download_all.py` | ✅ |

---

## Gap List: Missing Components

### Gap 1: HQ Original Download + Storage 🔴 HIGH PRIORITY

**Current**: URLs stored in taxon metadata; images not downloaded
**Needed**: Download actual HQ originals to object storage

**Implementation**:
```python
# Need to add to scraper.py:
- Download original (not just URL reference)
- Store in structured path: media/{source}/{media_id}/original.{ext}
- Generate derivatives (thumb/small/medium/large + webp)
```

### Gap 2: pHash Population 🟡 MEDIUM PRIORITY

**Current**: `perceptual_hash` column exists but is NOT populated
**Needed**: Compute pHash for all images during download

**Implementation**:
```python
# Need to add:
from imagehash import phash
from PIL import Image

pHash = str(phash(Image.open(image_path)))
```

### Gap 3: Near-Duplicate Detection 🟡 MEDIUM PRIORITY

**Current**: Only SHA-256 exact deduplication
**Needed**: pHash Hamming distance ≤ 6 for near-duplicate detection

**Implementation**:
```sql
-- Find near-duplicates
SELECT * FROM media.image 
WHERE hamming_distance(perceptual_hash, :target_hash) <= 6
```

### Gap 4: Quality Scoring Enhancement 🟡 MEDIUM PRIORITY

**Current**: Basic quality scoring (80-95 based on source)
**Needed**: Resolution, sharpness, compression artifact detection

**Implementation**:
```python
def compute_quality_score(image_path):
    # Resolution score (0-30)
    # Sharpness score (0-30) 
    # Noise/artifact score (0-20)
    # Color vibrancy score (0-20)
    return total_score  # 0-100
```

### Gap 5: Derivative Generation 🔴 HIGH PRIORITY

**Current**: Only original URL stored
**Needed**: Generate thumb/small/medium/large + webp versions

**Sizes**:
- thumb: 150x150
- small: 320px long edge
- medium: 640px long edge  
- large: 1280px long edge
- webp: all sizes

### Gap 6: HQ URL Resolution 🟡 MEDIUM PRIORITY

**Current iNat**: Uses `medium_url` in some cases
**Needed**: Always use `original_url` when available

```python
# iNat: Prefer original
url = photo.get("url", "").replace("square", "original")

# Wikimedia: Already gets original ✅
# GBIF: Take highest resolution from media list
```

### Gap 7: Training Dataset Views 🟢 LOW PRIORITY

**Current**: No training views
**Needed**: 
```sql
CREATE VIEW media.training_hq AS
SELECT * FROM media.image 
WHERE quality_score >= 70 
  AND license IN ('CC0', 'CC-BY', 'CC-BY-SA', 'public_domain');

CREATE VIEW media.training_general AS
SELECT * FROM media.image;
```

### Gap 8: Verification States 🟢 LOW PRIORITY

**Current**: Only `verified` boolean
**Needed**: `label_state` enum: `source_claimed | model_suggested | human_verified | disputed`

### Gap 9: Object Storage Integration 🔴 HIGH PRIORITY

**Current**: Local filesystem only (`C:/Users/.../mindex_images`)
**Needed**: S3/R2/Azure Blob with CDN

---

## Implementation Priority

### Phase 1: Critical HQ Features (This Sprint)

1. **Derivative Generation Module** - Create thumb/small/medium/large + webp
2. **pHash Computation** - Populate perceptual_hash during download
3. **Enhanced Quality Scoring** - Resolution + sharpness-based
4. **HQ URL Resolution** - Always get original_url from sources

### Phase 2: Robustness (Next Sprint)

5. **Near-Duplicate Detection** - pHash Hamming distance queries
6. **Training Dataset Views** - SQL views for ML training data
7. **Label States Migration** - Add label_state enum column

### Phase 3: Scale (Future)

8. **Object Storage** - S3/R2 integration
9. **CDN Configuration** - Cloudflare/CloudFront

---

## Files to Modify/Create

### Modify Existing

| File | Changes |
|------|---------|
| `mindex_etl/images/scraper.py` | Add pHash, derivatives, quality scoring |
| `mindex_etl/sources/multi_image.py` | Force original_url resolution |
| `mindex_api/routers/images.py` | Add derivative endpoints |

### Create New

| File | Purpose |
|------|---------|
| `mindex_etl/images/derivatives.py` | Derivative generation (webp, sizes) |
| `mindex_etl/images/quality.py` | Enhanced quality scoring |
| `mindex_etl/images/phash.py` | pHash computation + near-dup detection |
| `migrations/0006_hq_media.sql` | Training views, label_state |

---

## Default Thresholds (Confirmed)

| Threshold | Value | Notes |
|-----------|-------|-------|
| Minimum HQ long edge | 1600px | Reject below unless rare species |
| pHash near-dup distance | ≤ 6 | Hamming distance |
| quality_score HQ cutoff | ≥ 70 | For training_hq view |
| Rate limit (iNat) | 0.3s | Per request |
| Rate limit (web scraping) | 1.0s | Conservative |

---

*Document created: 2026-01-16*
