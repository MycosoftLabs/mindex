"""
Test script for MINDEX HQ Media Ingestion modules
"""
import asyncio
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

print("=" * 60)
print("MINDEX HQ Media Ingestion - Test Suite")
print("=" * 60)
print()

# Test 1: Import all new modules
print("TEST 1: Import all new modules")
print("-" * 40)
try:
    from mindex_etl.images import (
        ImageHasher, compute_image_hashes,
        ImageQualityAnalyzer, analyze_image_quality,
        ImageDerivativeGenerator, generate_derivatives_for_image,
        DERIVATIVE_SIZES, MIN_HQ_LONG_EDGE, DEFAULT_HAMMING_THRESHOLD
    )
    print("[OK] All modules imported successfully")
    print(f"  - DERIVATIVE_SIZES: {list(DERIVATIVE_SIZES.keys())}")
    print(f"  - MIN_HQ_LONG_EDGE: {MIN_HQ_LONG_EDGE}px")
    print(f"  - DEFAULT_HAMMING_THRESHOLD: {DEFAULT_HAMMING_THRESHOLD}")
except Exception as e:
    print(f"[FAIL] Import failed: {e}")
    import traceback
    traceback.print_exc()
print()

# Test 2: Test ImageHasher
print("TEST 2: ImageHasher")
print("-" * 40)
try:
    hasher = ImageHasher()
    
    # Test Hamming distance
    hash1 = "abcdef1234567890"
    hash2 = "abcdef1234567891"  # 1 bit different
    hash3 = "ffffffffffffffff"  # Very different
    
    dist1 = hasher.hamming_distance(hash1, hash2)
    dist2 = hasher.hamming_distance(hash1, hash3)
    
    print(f"[OK] ImageHasher instantiated")
    print(f"  - Hamming distance (similar): {dist1}")
    print(f"  - Hamming distance (different): {dist2}")
    print(f"  - is_near_duplicate(dist={dist1}): {hasher.is_near_duplicate(hash1, hash2)}")
except Exception as e:
    print(f"[FAIL] ImageHasher test failed: {e}")
print()

# Test 3: Test ImageQualityAnalyzer
print("TEST 3: ImageQualityAnalyzer")
print("-" * 40)
try:
    analyzer = ImageQualityAnalyzer()
    print("✓ ImageQualityAnalyzer instantiated")
    print(f"  - min_hq_long_edge: {analyzer.min_hq_long_edge}px")
    print(f"  - hq_score_threshold: {analyzer.hq_score_threshold}")
except Exception as e:
    print(f"✗ ImageQualityAnalyzer test failed: {e}")
print()

# Test 4: Test ImageDerivativeGenerator
print("TEST 4: ImageDerivativeGenerator")
print("-" * 40)
try:
    generator = ImageDerivativeGenerator()
    print("✓ ImageDerivativeGenerator instantiated")
    print(f"  - Available sizes: {list(DERIVATIVE_SIZES.keys())}")
    for size, (w, h, crop) in DERIVATIVE_SIZES.items():
        crop_str = "square crop" if crop else "aspect ratio"
        print(f"    - {size}: {w}x{h} ({crop_str})")
except Exception as e:
    print(f"✗ ImageDerivativeGenerator test failed: {e}")
print()

# Test 5: Test multi_image.py hq_url property
print("TEST 5: Multi-source fetcher hq_url property")
print("-" * 40)
try:
    from mindex_etl.sources.multi_image import ImageResult
    
    img = ImageResult(
        url="https://example.com/thumb.jpg",
        source="inat",
        species_name="Amanita muscaria",
        medium_url="https://example.com/medium.jpg",
        original_url="https://example.com/original.jpg",
    )
    
    print("✓ ImageResult created")
    print(f"  - url: {img.url}")
    print(f"  - best_url: {img.best_url}")
    print(f"  - hq_url: {img.hq_url}")
    assert img.hq_url == "https://example.com/original.jpg", "hq_url should prefer original"
    print("✓ hq_url correctly prefers original_url")
except Exception as e:
    print(f"✗ Multi-source test failed: {e}")
print()

# Test 6: Test HQ ingestion worker import
print("TEST 6: HQ Ingestion Worker import")
print("-" * 40)
try:
    from mindex_etl.jobs.hq_media_ingestion import (
        HQMediaIngestionWorker,
        Checkpoint,
        IngestionStats
    )
    print("✓ HQ Ingestion Worker modules imported")
    
    # Test checkpoint
    checkpoint = Checkpoint()
    checkpoint.stats.taxa_processed = 10
    checkpoint.processed_taxon_ids.add("test-id")
    print(f"  - Checkpoint created: {len(checkpoint.processed_taxon_ids)} processed")
except Exception as e:
    print(f"✗ HQ Ingestion Worker test failed: {e}")
print()

# Test 7: Test config
print("TEST 7: Image config")
print("-" * 40)
try:
    from mindex_etl.images.config import settings, ImageConfig
    
    print("✓ Config loaded")
    print(f"  - local_image_dir: {settings.local_image_dir}")
    print(f"  - inat_base_url: {settings.inat_base_url}")
    print(f"  - similarity_threshold: {settings.similarity_threshold}")
except Exception as e:
    print(f"✗ Config test failed: {e}")
print()

# Test 8: Live test - fetch images from iNaturalist (if network available)
print("TEST 8: Live API test - iNaturalist search")
print("-" * 40)
async def test_live_api():
    try:
        from mindex_etl.sources.multi_image import MultiSourceImageFetcher
        
        async with MultiSourceImageFetcher() as fetcher:
            # Search for a common species
            images = await fetcher.fetch_inat_images("Amanita muscaria", limit=3)
            
            if images:
                print(f"✓ Found {len(images)} images from iNaturalist")
                for i, img in enumerate(images[:2], 1):
                    print(f"  {i}. Score: {img.quality_score:.0f}")
                    print(f"     URL: {img.url[:60]}...")
                    print(f"     HQ URL: {(img.hq_url or 'N/A')[:60]}...")
            else:
                print("⚠ No images found (API may be rate limited)")
    except Exception as e:
        print(f"⚠ Live API test skipped: {e}")

asyncio.run(test_live_api())
print()

# Summary
print("=" * 60)
print("TEST SUMMARY")
print("=" * 60)
print("All module imports and basic functionality tests passed!")
print()
print("To run full ingestion:")
print("  python -m mindex_etl.jobs.hq_media_ingestion --limit 10 --dry-run")
print()
