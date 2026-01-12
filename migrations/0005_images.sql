-- migrations/0005_images.sql
-- MINDEX Fungal Image Database Schema
-- Target: World's largest searchable fungal image database

-- ============================================================================
-- CORE IMAGE TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS media.image (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Identification
    mindex_id VARCHAR(50) UNIQUE NOT NULL,  -- MYCO-IMG-XXXXXXXX format
    filename VARCHAR(500) NOT NULL,
    original_filename VARCHAR(500),
    
    -- File Info
    file_path TEXT NOT NULL,
    file_size_bytes BIGINT,
    width INTEGER,
    height INTEGER,
    format VARCHAR(20),  -- jpg, png, webp, etc.
    
    -- Content Hash (for deduplication)
    content_hash VARCHAR(64) UNIQUE,  -- SHA-256 hash
    perceptual_hash VARCHAR(64),      -- pHash for visual similarity
    
    -- Source Information
    source VARCHAR(100) NOT NULL,  -- inaturalist, wikipedia, flickr, pinterest, etc.
    source_url TEXT,
    source_id VARCHAR(255),
    license VARCHAR(100),
    attribution TEXT,
    
    -- Species Matching
    taxon_id UUID REFERENCES core.taxon(id),
    species_confidence DECIMAL(5,4),  -- 0.0000 to 1.0000 (98%+ target)
    species_match_method VARCHAR(50),  -- ml_model, metadata, manual, api
    verified BOOLEAN DEFAULT FALSE,
    verified_by UUID,
    verified_at TIMESTAMP WITH TIME ZONE,
    
    -- Categorization
    image_type VARCHAR(50),  -- field, lab, petri, microscope, macro, habitat
    subject_type VARCHAR(50),  -- mushroom, mycelium, spore, mold, yeast, mildew
    environment VARCHAR(50),  -- forest, grassland, lab, petri_dish, substrate
    growth_stage VARCHAR(50),  -- primordia, button, mature, decaying, sporing
    
    -- Metadata
    capture_date DATE,
    capture_location GEOGRAPHY(POINT, 4326),
    location_name TEXT,
    photographer VARCHAR(255),
    camera_info TEXT,
    
    -- ML Features
    embedding VECTOR(512),  -- For similarity search
    color_histogram JSONB,
    detected_features JSONB,  -- ML-detected features
    
    -- Associations
    associated_chemicals TEXT[],
    associated_plants TEXT[],
    associated_animals TEXT[],
    associated_environments TEXT[],
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================================
-- INDEXES FOR FAST SEARCHING
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_image_taxon ON media.image(taxon_id);
CREATE INDEX IF NOT EXISTS idx_image_source ON media.image(source);
CREATE INDEX IF NOT EXISTS idx_image_type ON media.image(image_type);
CREATE INDEX IF NOT EXISTS idx_image_subject ON media.image(subject_type);
CREATE INDEX IF NOT EXISTS idx_image_confidence ON media.image(species_confidence DESC);
CREATE INDEX IF NOT EXISTS idx_image_hash ON media.image(content_hash);
CREATE INDEX IF NOT EXISTS idx_image_phash ON media.image(perceptual_hash);
CREATE INDEX IF NOT EXISTS idx_image_mindex_id ON media.image(mindex_id);
CREATE INDEX IF NOT EXISTS idx_image_verified ON media.image(verified);
CREATE INDEX IF NOT EXISTS idx_image_date ON media.image(capture_date);

-- Full-text search on filename and location
CREATE INDEX IF NOT EXISTS idx_image_fts ON media.image 
    USING GIN (to_tsvector('english', COALESCE(filename, '') || ' ' || COALESCE(location_name, '')));

-- Spatial index for location searches
CREATE INDEX IF NOT EXISTS idx_image_location ON media.image USING GIST(capture_location);

-- ============================================================================
-- IMAGE COLLECTIONS (for grouping related images)
-- ============================================================================

CREATE TABLE IF NOT EXISTS media.image_collection (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    collection_type VARCHAR(50),  -- species, research, observation, experiment
    taxon_id UUID REFERENCES core.taxon(id),
    image_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS media.image_collection_item (
    collection_id UUID REFERENCES media.image_collection(id) ON DELETE CASCADE,
    image_id UUID REFERENCES media.image(id) ON DELETE CASCADE,
    position INTEGER,
    added_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (collection_id, image_id)
);

-- ============================================================================
-- IMAGE SIMILARITY (for finding related images)
-- ============================================================================

CREATE TABLE IF NOT EXISTS media.image_similarity (
    image_id_1 UUID REFERENCES media.image(id) ON DELETE CASCADE,
    image_id_2 UUID REFERENCES media.image(id) ON DELETE CASCADE,
    similarity_score DECIMAL(5,4),
    similarity_type VARCHAR(50),  -- visual, species, location
    PRIMARY KEY (image_id_1, image_id_2)
);

-- ============================================================================
-- SCRAPING STATUS TRACKING
-- ============================================================================

CREATE TABLE IF NOT EXISTS media.scrape_job (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(100) NOT NULL,
    query VARCHAR(500),
    status VARCHAR(50) DEFAULT 'pending',  -- pending, running, completed, failed
    images_found INTEGER DEFAULT 0,
    images_downloaded INTEGER DEFAULT 0,
    images_deduplicated INTEGER DEFAULT 0,
    images_matched INTEGER DEFAULT 0,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================================
-- VIEWS FOR STATISTICS
-- ============================================================================

CREATE OR REPLACE VIEW media.image_stats AS
SELECT 
    COUNT(*) as total_images,
    COUNT(DISTINCT taxon_id) as species_with_images,
    COUNT(CASE WHEN verified THEN 1 END) as verified_images,
    COUNT(CASE WHEN species_confidence >= 0.98 THEN 1 END) as high_confidence_images,
    SUM(file_size_bytes) as total_storage_bytes,
    AVG(species_confidence) as avg_confidence
FROM media.image;

CREATE OR REPLACE VIEW media.image_by_source AS
SELECT 
    source,
    COUNT(*) as image_count,
    COUNT(DISTINCT taxon_id) as species_count,
    AVG(species_confidence) as avg_confidence
FROM media.image
GROUP BY source
ORDER BY image_count DESC;

CREATE OR REPLACE VIEW media.image_by_type AS
SELECT 
    image_type,
    subject_type,
    COUNT(*) as count
FROM media.image
GROUP BY image_type, subject_type
ORDER BY count DESC;

-- ============================================================================
-- TRIGGERS
-- ============================================================================

CREATE OR REPLACE FUNCTION update_image_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_image_updated_at
BEFORE UPDATE ON media.image
FOR EACH ROW
EXECUTE FUNCTION update_image_updated_at();

-- Update collection image count
CREATE OR REPLACE FUNCTION update_collection_count()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE media.image_collection SET image_count = image_count + 1 WHERE id = NEW.collection_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE media.image_collection SET image_count = image_count - 1 WHERE id = OLD.collection_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_collection_count
AFTER INSERT OR DELETE ON media.image_collection_item
FOR EACH ROW
EXECUTE FUNCTION update_collection_count();

-- ============================================================================
-- INITIAL DATA: Create media schema if not exists
-- ============================================================================

-- Note: Run this before the rest of the migration if schema doesn't exist
-- CREATE SCHEMA IF NOT EXISTS media;

COMMENT ON TABLE media.image IS 'World largest searchable fungal image database';
COMMENT ON COLUMN media.image.mindex_id IS 'Unique MINDEX ID in format MYCO-IMG-XXXXXXXX';
COMMENT ON COLUMN media.image.content_hash IS 'SHA-256 hash for exact deduplication';
COMMENT ON COLUMN media.image.perceptual_hash IS 'Perceptual hash for visual similarity matching';
COMMENT ON COLUMN media.image.species_confidence IS 'ML model confidence score (0-1, target 98%+)';
