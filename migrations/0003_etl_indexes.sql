-- ETL Indexes and Constraints
-- ============================
-- Additional indexes for ETL job performance and data integrity

BEGIN;

-- Unique constraint on observations for deduplication
CREATE UNIQUE INDEX IF NOT EXISTS uq_observation_source_id 
    ON obs.observation (source, source_id)
    WHERE source_id IS NOT NULL;

-- Index for taxon search by source
CREATE INDEX IF NOT EXISTS idx_taxon_source ON core.taxon (source);

-- Index for observation queries by time
CREATE INDEX IF NOT EXISTS idx_observation_observed_at 
    ON obs.observation (observed_at DESC);

-- Index for observation queries by source
CREATE INDEX IF NOT EXISTS idx_observation_source ON obs.observation (source);

-- Partial index for research-grade observations
CREATE INDEX IF NOT EXISTS idx_observation_quality 
    ON obs.observation ((metadata->>'quality_grade'))
    WHERE metadata->>'quality_grade' IS NOT NULL;

-- Add traits indexes
CREATE INDEX IF NOT EXISTS idx_taxon_trait_source ON bio.taxon_trait (source);

-- Add external ID lookup index
CREATE INDEX IF NOT EXISTS idx_taxon_external_source_lookup 
    ON core.taxon_external_id (source, external_id);

COMMIT;
