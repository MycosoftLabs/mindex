-- Entity Delta Storage Migration - Feb 13, 2026

CREATE SCHEMA IF NOT EXISTS crep;

CREATE TABLE IF NOT EXISTS crep.entity_keyframes (
  entity_id TEXT NOT NULL,
  keyframe_at TIMESTAMPTZ NOT NULL,
  full_state JSONB NOT NULL,
  PRIMARY KEY (entity_id, keyframe_at)
);

CREATE TABLE IF NOT EXISTS crep.entity_deltas (
  id BIGSERIAL,
  entity_id TEXT NOT NULL,
  delta_at TIMESTAMPTZ NOT NULL,
  delta JSONB NOT NULL,
  PRIMARY KEY (id, delta_at)
) PARTITION BY RANGE (delta_at);

DO $$
DECLARE
  month_start DATE := DATE_TRUNC('month', NOW())::DATE;
  month_end DATE := (DATE_TRUNC('month', NOW()) + INTERVAL '1 month')::DATE;
  next_month_end DATE := (DATE_TRUNC('month', NOW()) + INTERVAL '2 months')::DATE;
BEGIN
  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS crep.entity_deltas_%s PARTITION OF crep.entity_deltas FOR VALUES FROM (%L) TO (%L)',
    TO_CHAR(month_start, 'YYYYMM'),
    month_start,
    month_end
  );

  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS crep.entity_deltas_%s PARTITION OF crep.entity_deltas FOR VALUES FROM (%L) TO (%L)',
    TO_CHAR(month_end, 'YYYYMM'),
    month_end,
    next_month_end
  );
END $$;

CREATE INDEX IF NOT EXISTS idx_deltas_entity_time ON crep.entity_deltas (entity_id, delta_at);
CREATE INDEX IF NOT EXISTS idx_keyframes_entity_time ON crep.entity_keyframes (entity_id, keyframe_at DESC);
