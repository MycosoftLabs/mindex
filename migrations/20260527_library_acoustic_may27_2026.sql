-- MINDEX Library: sensor blobs + ingest manifests (Request 010/012, May 27 2026)
CREATE SCHEMA IF NOT EXISTS library;

CREATE TABLE IF NOT EXISTS library.manifest (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'acoustic',
    run_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_finished_at TIMESTAMPTZ,
    files_registered INTEGER NOT NULL DEFAULT 0,
    bytes_total BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS library.blob (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'acoustic',
    sensor_type TEXT,
    rel_path TEXT NOT NULL,
    abs_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes BIGINT,
    duration_sec DOUBLE PRECISION,
    sample_rate_hz INTEGER,
    channels INTEGER,
    format TEXT,
    codec TEXT,
    playback_class TEXT NOT NULL DEFAULT 'audio',
    license TEXT,
    needs_transcode BOOLEAN NOT NULL DEFAULT FALSE,
    unsupported_codec BOOLEAN NOT NULL DEFAULT FALSE,
    manifest_id UUID REFERENCES library.manifest(id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT library_blob_content_hash_unique UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS idx_library_blob_category ON library.blob(category);
CREATE INDEX IF NOT EXISTS idx_library_blob_source ON library.blob(source_id);
CREATE INDEX IF NOT EXISTS idx_library_blob_sensor ON library.blob(sensor_type);
CREATE INDEX IF NOT EXISTS idx_library_blob_filename ON library.blob(filename);

GRANT USAGE ON SCHEMA library TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA library TO mindex;
ALTER DEFAULT PRIVILEGES IN SCHEMA library GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mindex;
