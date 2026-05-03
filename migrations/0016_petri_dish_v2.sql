-- Petri Dish v2 persistence — May 02, 2026
-- Schema petri_v2: sessions, frames, chemistry, recordings, AI tracks (MINDEX).

BEGIN;

CREATE SCHEMA IF NOT EXISTS petri_v2;

CREATE TABLE IF NOT EXISTS petri_v2.sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_session_id VARCHAR(128) UNIQUE NOT NULL,
    seed_hex VARCHAR(128),
    grid_w INT NOT NULL DEFAULT 128,
    grid_h INT NOT NULL DEFAULT 128,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS petri_v2.session_inputs (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES petri_v2.sessions(id) ON DELETE CASCADE,
    frame_idx BIGINT NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS petri_v2.frames (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES petri_v2.sessions(id) ON DELETE CASCADE,
    frame_idx BIGINT NOT NULL,
    ts_us BIGINT,
    tip_count INT,
    organism_count INT,
    mean_sugar DOUBLE PRECISION,
    mean_nitrogen DOUBLE PRECISION,
    chemistry_means JSONB DEFAULT '[]'::jsonb,
    snapshot JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (session_id, frame_idx)
);

CREATE TABLE IF NOT EXISTS petri_v2.organisms (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES petri_v2.sessions(id) ON DELETE CASCADE,
    organism_class VARCHAR(32) NOT NULL,
    species_key TEXT NOT NULL,
    lineage JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS petri_v2.chemistry_samples (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES petri_v2.sessions(id) ON DELETE CASCADE,
    frame_idx BIGINT NOT NULL,
    compound_idx INT NOT NULL,
    mean_val DOUBLE PRECISION,
    std_val DOUBLE PRECISION,
    max_val DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS petri_v2.recordings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES petri_v2.sessions(id) ON DELETE CASCADE,
    format VARCHAR(16) NOT NULL,
    blob_url TEXT,
    duration_s DOUBLE PRECISION,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS petri_v2.ai_tracks (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES petri_v2.sessions(id) ON DELETE CASCADE,
    frame_idx BIGINT NOT NULL,
    mask_blob_url TEXT,
    tracks JSONB DEFAULT '[]'::jsonb,
    metrics JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_petri_v2_frames_session ON petri_v2.frames(session_id, frame_idx DESC);
CREATE INDEX IF NOT EXISTS idx_petri_v2_ai_session ON petri_v2.ai_tracks(session_id, frame_idx DESC);

COMMIT;
