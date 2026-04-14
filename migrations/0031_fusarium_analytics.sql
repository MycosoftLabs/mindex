-- FUSARIUM analytics schema
-- Apr 2026

CREATE SCHEMA IF NOT EXISTS fusarium;

CREATE TABLE IF NOT EXISTS fusarium.entity_tracks (
    track_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    latest_label TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_position GEOGRAPHY(POINT, 4326)
);

CREATE INDEX IF NOT EXISTS idx_fusarium_entity_tracks_last_seen
    ON fusarium.entity_tracks (last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_fusarium_entity_tracks_position
    ON fusarium.entity_tracks USING GIST (last_position);

CREATE TABLE IF NOT EXISTS fusarium.correlation_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID,
    domains TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fusarium_corr_created_at
    ON fusarium.correlation_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fusarium_corr_entity
    ON fusarium.correlation_events (entity_id);
