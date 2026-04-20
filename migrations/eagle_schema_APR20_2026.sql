-- Eagle Eye (CREP / video intelligence) — Apr 20, 2026
-- Requires: postgis (existing), pgvector for VECTOR columns.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS eagle;

CREATE TABLE IF NOT EXISTS eagle.video_sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    stable_location BOOLEAN NOT NULL,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    location_confidence REAL,
    stream_url TEXT,
    embed_url TEXT,
    media_url TEXT,
    source_status TEXT,
    permissions JSONB,
    retention_policy JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS eagle_vs_kind_provider
    ON eagle.video_sources (kind, provider);

CREATE INDEX IF NOT EXISTS eagle_vs_location_gix
    ON eagle.video_sources
    USING GIST (ST_SetSRID(ST_MakePoint(lng, lat), 4326))
    WHERE lat IS NOT NULL AND lng IS NOT NULL;

CREATE TABLE IF NOT EXISTS eagle.video_events (
    id TEXT PRIMARY KEY,
    video_source_id TEXT REFERENCES eagle.video_sources (id) ON DELETE SET NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    start_at TIMESTAMPTZ,
    end_at TIMESTAMPTZ,
    native_place TEXT,
    inferred_place TEXT,
    inference_confidence REAL,
    text_context TEXT,
    thumbnail_url TEXT,
    clip_ref TEXT,
    raw_metadata JSONB
);

CREATE INDEX IF NOT EXISTS eagle_ve_time ON eagle.video_events (observed_at DESC);

CREATE TABLE IF NOT EXISTS eagle.object_tracks (
    id BIGSERIAL PRIMARY KEY,
    video_event_id TEXT REFERENCES eagle.video_events (id) ON DELETE CASCADE,
    class_label TEXT,
    open_vocab_label TEXT,
    track_id INTEGER,
    bbox_series JSONB,
    mask_series JSONB,
    confidence_series JSONB,
    reid_embedding vector(512),
    alert_flags TEXT[]
);

CREATE INDEX IF NOT EXISTS eagle_ot_event ON eagle.object_tracks (video_event_id);

CREATE TABLE IF NOT EXISTS eagle.scene_index (
    id BIGSERIAL PRIMARY KEY,
    video_event_id TEXT REFERENCES eagle.video_events (id) ON DELETE CASCADE,
    transcript TEXT,
    ocr_text TEXT,
    vlm_summary TEXT,
    embedding vector(768)
);

CREATE INDEX IF NOT EXISTS eagle_si_event ON eagle.scene_index (video_event_id);
