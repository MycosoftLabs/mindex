-- CREP project-scoped nature JSON cache (iNat, etc.) — Apr 22, 2026
-- Purpose: (a) iNat preload — website routes read crep.project_nature_cache first, fall back to live iNaturalist API.
-- Writers: MAS scheduled job, MINDEX ETL, or service role. Readers: MINDEX API or website BFF.
-- See docs/CREP_PIPELINE_AUDIT.md (MINDEX) and WEBSITE docs for handoff.

CREATE SCHEMA IF NOT EXISTS crep;

CREATE TABLE IF NOT EXISTS crep.project_nature_cache (
    project_key   TEXT        NOT NULL,
    cache_key     TEXT        NOT NULL,
    payload       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NULL,
    source_label  TEXT        NULL,
    PRIMARY KEY (project_key, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_project_nature_cache_updated
    ON crep.project_nature_cache (project_key, updated_at DESC);

COMMENT ON TABLE crep.project_nature_cache IS
  'Bbox/project-scaled nature data (e.g. iNat GeoJSON) cached for Oyster, Goffs, and future CREP subprojects.';

COMMENT ON COLUMN crep.project_nature_cache.project_key IS
  'Logical project: e.g. oyster, goffs, mojave.';
COMMENT ON COLUMN crep.project_nature_cache.cache_key IS
  'Slot within project: e.g. inat, inat_11013500, or hash(bbox+taxon).';
COMMENT ON COLUMN crep.project_nature_cache.expires_at IS
  'Optional; NULL means rely on MAS/ETL refresh cadence.';
