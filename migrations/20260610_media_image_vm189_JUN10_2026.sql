-- Full media.image schema for VM 189 without pgvector (extension unavailable in postgis image).
-- Run after 20260610_media_image_upgrade_JUN10_2026.sql (drops empty bootstrap).

CREATE TABLE IF NOT EXISTS media.image (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mindex_id VARCHAR(50) UNIQUE NOT NULL,
    filename VARCHAR(500) NOT NULL,
    original_filename VARCHAR(500),
    file_path TEXT NOT NULL,
    file_size_bytes BIGINT,
    width INTEGER,
    height INTEGER,
    format VARCHAR(20),
    content_hash VARCHAR(64) UNIQUE,
    perceptual_hash VARCHAR(64),
    source VARCHAR(100) NOT NULL,
    source_url TEXT,
    source_id VARCHAR(255),
    license VARCHAR(100),
    attribution TEXT,
    taxon_id UUID REFERENCES core.taxon(id),
    species_confidence DECIMAL(5,4),
    species_match_method VARCHAR(50),
    verified BOOLEAN DEFAULT FALSE,
    verified_by UUID,
    verified_at TIMESTAMPTZ,
    image_type VARCHAR(50),
    subject_type VARCHAR(50),
    environment VARCHAR(50),
    growth_stage VARCHAR(50),
    capture_date DATE,
    capture_location GEOGRAPHY(POINT, 4326),
    location_name TEXT,
    photographer VARCHAR(255),
    camera_info TEXT,
    embedding_json JSONB,
    color_histogram JSONB,
    detected_features JSONB,
    associated_chemicals TEXT[],
    associated_plants TEXT[],
    associated_animals TEXT[],
    associated_environments TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    scraped_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_image_taxon ON media.image(taxon_id);
CREATE INDEX IF NOT EXISTS idx_image_source ON media.image(source);
CREATE INDEX IF NOT EXISTS idx_image_content_hash ON media.image(content_hash);

GRANT SELECT, INSERT, UPDATE, DELETE ON media.image TO mindex;
GRANT USAGE ON SCHEMA media TO mindex;
