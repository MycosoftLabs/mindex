-- Unified Entity Schema Migration - Feb 13, 2026

CREATE SCHEMA IF NOT EXISTS crep;

CREATE TABLE IF NOT EXISTS crep.unified_entities (
  id TEXT NOT NULL,
  entity_type VARCHAR(50) NOT NULL,
  geometry GEOGRAPHY(GEOMETRY, 4326) NOT NULL,
  state JSONB NOT NULL DEFAULT '{}'::jsonb,
  observed_at TIMESTAMPTZ NOT NULL,
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
  source VARCHAR(100) NOT NULL,
  properties JSONB NOT NULL DEFAULT '{}'::jsonb,
  s2_cell_id BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (id, observed_at)
) PARTITION BY RANGE (observed_at);

DO $$
DECLARE
  month_start DATE := DATE_TRUNC('month', NOW())::DATE;
  month_end DATE := (DATE_TRUNC('month', NOW()) + INTERVAL '1 month')::DATE;
  next_month_end DATE := (DATE_TRUNC('month', NOW()) + INTERVAL '2 months')::DATE;
BEGIN
  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS crep.unified_entities_%s PARTITION OF crep.unified_entities FOR VALUES FROM (%L) TO (%L)',
    TO_CHAR(month_start, 'YYYYMM'),
    month_start,
    month_end
  );

  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS crep.unified_entities_%s PARTITION OF crep.unified_entities FOR VALUES FROM (%L) TO (%L)',
    TO_CHAR(month_end, 'YYYYMM'),
    month_end,
    next_month_end
  );
END $$;

CREATE INDEX IF NOT EXISTS idx_unified_s2_cell ON crep.unified_entities (s2_cell_id);
CREATE INDEX IF NOT EXISTS idx_unified_type_time ON crep.unified_entities (entity_type, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_unified_geometry ON crep.unified_entities USING GIST (geometry);
