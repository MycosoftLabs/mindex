-- migrations/0006_hq_media_enhancements.sql
-- MINDEX HQ Media Enhancements
-- Adds training views, label states, quality scoring, and near-duplicate detection

-- ============================================================================
-- 1. ADD LABEL STATE ENUM
-- ============================================================================

DO $$ BEGIN
    CREATE TYPE media.label_state AS ENUM (
        'source_claimed',      -- Label from original source (iNat, GBIF, etc.)
        'model_suggested',     -- ML model suggested this label
        'human_verified',      -- Human expert verified
        'disputed'             -- Label is contested
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Add label_state column if not exists
ALTER TABLE media.image 
ADD COLUMN IF NOT EXISTS label_state media.label_state DEFAULT 'source_claimed';

-- ============================================================================
-- 2. ADD QUALITY SCORING COLUMNS
-- ============================================================================

-- Quality score (0-100)
ALTER TABLE media.image 
ADD COLUMN IF NOT EXISTS quality_score DECIMAL(5,2) DEFAULT NULL;

-- Component scores for debugging/analysis
ALTER TABLE media.image 
ADD COLUMN IF NOT EXISTS resolution_score DECIMAL(5,2) DEFAULT NULL;

ALTER TABLE media.image 
ADD COLUMN IF NOT EXISTS sharpness_score DECIMAL(5,2) DEFAULT NULL;

ALTER TABLE media.image 
ADD COLUMN IF NOT EXISTS noise_score DECIMAL(5,2) DEFAULT NULL;

ALTER TABLE media.image 
ADD COLUMN IF NOT EXISTS color_score DECIMAL(5,2) DEFAULT NULL;

-- ============================================================================
-- 3. ADD DERIVATIVE TRACKING
-- ============================================================================

-- JSON map of derivative paths
ALTER TABLE media.image 
ADD COLUMN IF NOT EXISTS derivatives JSONB DEFAULT '{}'::jsonb;
-- Format: {"thumb": "path", "small": "path", "medium": "path", "large": "path", "webp": {...}}

-- Original file storage URI (S3/R2/local)
ALTER TABLE media.image 
ADD COLUMN IF NOT EXISTS storage_original_uri TEXT;

-- ============================================================================
-- 4. ADD LICENSE COMPLIANCE TRACKING
-- ============================================================================

-- License compliance flag
ALTER TABLE media.image 
ADD COLUMN IF NOT EXISTS license_compliant BOOLEAN DEFAULT TRUE;

-- Acceptable licenses for training
COMMENT ON COLUMN media.image.license_compliant IS 
    'TRUE if license is acceptable for training (CC0, CC-BY, CC-BY-SA, public domain)';

-- ============================================================================
-- 5. HAMMING DISTANCE FUNCTION FOR NEAR-DUPLICATE DETECTION
-- ============================================================================

CREATE OR REPLACE FUNCTION media.hamming_distance(hash1 TEXT, hash2 TEXT)
RETURNS INTEGER AS $$
DECLARE
    i INTEGER;
    distance INTEGER := 0;
    len INTEGER;
    c1 CHAR;
    c2 CHAR;
    v1 INTEGER;
    v2 INTEGER;
    xor_val INTEGER;
BEGIN
    IF hash1 IS NULL OR hash2 IS NULL THEN
        RETURN 999;
    END IF;
    
    len := LEAST(length(hash1), length(hash2));
    
    FOR i IN 1..len LOOP
        c1 := substring(hash1 from i for 1);
        c2 := substring(hash2 from i for 1);
        
        -- Convert hex char to integer
        v1 := CASE 
            WHEN c1 BETWEEN '0' AND '9' THEN ascii(c1) - ascii('0')
            WHEN c1 BETWEEN 'a' AND 'f' THEN ascii(c1) - ascii('a') + 10
            WHEN c1 BETWEEN 'A' AND 'F' THEN ascii(c1) - ascii('A') + 10
            ELSE 0
        END;
        v2 := CASE 
            WHEN c2 BETWEEN '0' AND '9' THEN ascii(c2) - ascii('0')
            WHEN c2 BETWEEN 'a' AND 'f' THEN ascii(c2) - ascii('a') + 10
            WHEN c2 BETWEEN 'A' AND 'F' THEN ascii(c2) - ascii('A') + 10
            ELSE 0
        END;
        
        -- XOR and count bits
        xor_val := v1 # v2;  -- # is XOR in PostgreSQL
        
        -- Count set bits (popcount for 4-bit value)
        distance := distance + (
            ((xor_val >> 0) & 1) +
            ((xor_val >> 1) & 1) +
            ((xor_val >> 2) & 1) +
            ((xor_val >> 3) & 1)
        );
    END LOOP;
    
    RETURN distance;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION media.hamming_distance IS 
    'Compute Hamming distance between two hex hash strings for near-duplicate detection';

-- ============================================================================
-- 6. TRAINING DATASET VIEWS
-- ============================================================================

-- HQ Training Dataset: High quality, license-compliant images
CREATE OR REPLACE VIEW media.training_hq AS
SELECT 
    i.id,
    i.mindex_id,
    i.file_path,
    i.storage_original_uri,
    i.derivatives,
    i.source,
    i.taxon_id,
    t.canonical_name,
    t.rank,
    i.quality_score,
    i.width,
    i.height,
    i.license,
    i.label_state,
    i.verified,
    i.image_type,
    i.subject_type,
    i.embedding
FROM media.image i
LEFT JOIN core.taxon t ON i.taxon_id = t.id
WHERE i.quality_score >= 70
  AND i.license_compliant = TRUE
  AND GREATEST(i.width, i.height) >= 1600
  AND i.taxon_id IS NOT NULL;

COMMENT ON VIEW media.training_hq IS 
    'High-quality training dataset: quality_score >= 70, license compliant, min 1600px';

-- General Training Dataset: All images with labels
CREATE OR REPLACE VIEW media.training_general AS
SELECT 
    i.id,
    i.mindex_id,
    i.file_path,
    i.storage_original_uri,
    i.derivatives,
    i.source,
    i.taxon_id,
    t.canonical_name,
    t.rank,
    i.quality_score,
    i.width,
    i.height,
    i.license,
    i.label_state,
    i.verified,
    i.image_type,
    i.subject_type
FROM media.image i
LEFT JOIN core.taxon t ON i.taxon_id = t.id
WHERE i.taxon_id IS NOT NULL;

COMMENT ON VIEW media.training_general IS 
    'General training dataset: all labeled images regardless of quality';

-- Verified Images: Human-verified labels only
CREATE OR REPLACE VIEW media.training_verified AS
SELECT 
    i.id,
    i.mindex_id,
    i.file_path,
    i.storage_original_uri,
    i.derivatives,
    i.source,
    i.taxon_id,
    t.canonical_name,
    t.rank,
    i.quality_score,
    i.width,
    i.height,
    i.license,
    i.verified_by,
    i.verified_at
FROM media.image i
LEFT JOIN core.taxon t ON i.taxon_id = t.id
WHERE i.label_state = 'human_verified'
  AND i.verified = TRUE;

COMMENT ON VIEW media.training_verified IS 
    'Human-verified training dataset: highest confidence labels';

-- ============================================================================
-- 7. NEAR-DUPLICATE DETECTION VIEW
-- ============================================================================

-- Find potential duplicates (requires pHash to be populated)
CREATE OR REPLACE VIEW media.potential_duplicates AS
SELECT 
    i1.id AS image_id_1,
    i2.id AS image_id_2,
    i1.mindex_id AS mindex_id_1,
    i2.mindex_id AS mindex_id_2,
    i1.source AS source_1,
    i2.source AS source_2,
    media.hamming_distance(i1.perceptual_hash, i2.perceptual_hash) AS hamming_distance,
    CASE 
        WHEN i1.content_hash = i2.content_hash THEN 'exact'
        ELSE 'near'
    END AS duplicate_type
FROM media.image i1
JOIN media.image i2 ON i1.id < i2.id  -- Avoid self-join and duplicates
WHERE i1.perceptual_hash IS NOT NULL
  AND i2.perceptual_hash IS NOT NULL
  AND (
      i1.content_hash = i2.content_hash  -- Exact duplicate
      OR media.hamming_distance(i1.perceptual_hash, i2.perceptual_hash) <= 6  -- Near duplicate
  );

COMMENT ON VIEW media.potential_duplicates IS 
    'View of potential duplicate image pairs (exact or pHash distance <= 6)';

-- ============================================================================
-- 8. ENHANCED STATISTICS VIEWS
-- ============================================================================

-- Quality distribution
CREATE OR REPLACE VIEW media.quality_distribution AS
SELECT 
    CASE 
        WHEN quality_score >= 90 THEN 'excellent'
        WHEN quality_score >= 70 THEN 'hq'
        WHEN quality_score >= 50 THEN 'acceptable'
        WHEN quality_score >= 30 THEN 'low'
        ELSE 'poor'
    END AS quality_tier,
    COUNT(*) AS image_count,
    AVG(quality_score) AS avg_score,
    COUNT(DISTINCT taxon_id) AS unique_taxa
FROM media.image
WHERE quality_score IS NOT NULL
GROUP BY 1
ORDER BY avg_score DESC;

-- License distribution
CREATE OR REPLACE VIEW media.license_distribution AS
SELECT 
    license,
    license_compliant,
    COUNT(*) AS image_count,
    COUNT(DISTINCT taxon_id) AS unique_taxa
FROM media.image
GROUP BY license, license_compliant
ORDER BY image_count DESC;

-- Label state distribution
CREATE OR REPLACE VIEW media.label_state_distribution AS
SELECT 
    label_state,
    COUNT(*) AS image_count,
    AVG(quality_score) AS avg_quality
FROM media.image
GROUP BY label_state
ORDER BY image_count DESC;

-- ============================================================================
-- 9. INDEXES FOR NEW COLUMNS
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_image_quality_score ON media.image(quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_image_label_state ON media.image(label_state);
CREATE INDEX IF NOT EXISTS idx_image_license_compliant ON media.image(license_compliant);
CREATE INDEX IF NOT EXISTS idx_image_storage_uri ON media.image(storage_original_uri);

-- Partial index for HQ images (faster training queries)
CREATE INDEX IF NOT EXISTS idx_image_training_hq ON media.image(taxon_id, quality_score)
WHERE quality_score >= 70 AND license_compliant = TRUE;

-- ============================================================================
-- 10. UPDATE LICENSE COMPLIANCE FOR EXISTING RECORDS
-- ============================================================================

-- Set license_compliant based on known acceptable licenses
UPDATE media.image
SET license_compliant = TRUE
WHERE license IS NOT NULL AND license IN (
    'CC0', 'cc0', 'CC0-1.0',
    'CC-BY', 'cc-by', 'CC-BY-4.0', 'CC-BY-3.0', 'CC-BY-2.0',
    'CC-BY-SA', 'cc-by-sa', 'CC-BY-SA-4.0', 'CC-BY-SA-3.0', 'CC-BY-SA-2.0',
    'public_domain', 'PD', 'publicdomain',
    'CC-BY-NC', 'cc-by-nc'  -- Non-commercial is OK for research
);

UPDATE media.image
SET license_compliant = FALSE
WHERE license IS NOT NULL AND license IN (
    'all-rights-reserved', 'copyright', 'ARR',
    'CC-BY-ND', 'CC-BY-NC-ND'  -- No derivatives not OK for training
);

-- ============================================================================
-- DONE
-- ============================================================================

COMMENT ON TABLE media.image IS 
    'World largest searchable fungal image database with HQ training support';
