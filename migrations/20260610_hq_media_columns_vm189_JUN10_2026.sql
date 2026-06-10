-- HQ media columns for VM 189 (no pgvector / no embedding-dependent views).

DO $$ BEGIN
    CREATE TYPE media.label_state AS ENUM (
        'source_claimed', 'model_suggested', 'human_verified', 'disputed'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE media.image ADD COLUMN IF NOT EXISTS label_state media.label_state DEFAULT 'source_claimed';
ALTER TABLE media.image ADD COLUMN IF NOT EXISTS quality_score DECIMAL(5,2) DEFAULT NULL;
ALTER TABLE media.image ADD COLUMN IF NOT EXISTS resolution_score DECIMAL(5,2) DEFAULT NULL;
ALTER TABLE media.image ADD COLUMN IF NOT EXISTS sharpness_score DECIMAL(5,2) DEFAULT NULL;
ALTER TABLE media.image ADD COLUMN IF NOT EXISTS noise_score DECIMAL(5,2) DEFAULT NULL;
ALTER TABLE media.image ADD COLUMN IF NOT EXISTS color_score DECIMAL(5,2) DEFAULT NULL;
ALTER TABLE media.image ADD COLUMN IF NOT EXISTS derivatives JSONB DEFAULT '{}'::jsonb;
ALTER TABLE media.image ADD COLUMN IF NOT EXISTS storage_original_uri TEXT;
ALTER TABLE media.image ADD COLUMN IF NOT EXISTS license_compliant BOOLEAN DEFAULT TRUE;

GRANT SELECT, INSERT, UPDATE, DELETE ON media.image TO mindex;
