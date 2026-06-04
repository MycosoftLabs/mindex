-- SINE acoustic analysis stack (May 27, 2026)
-- Detectors, analysis runs, events, visualisation cache

-- Label columns (idempotent with 20260604)
ALTER TABLE library.blob
    ADD COLUMN IF NOT EXISTS title TEXT,
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS label_primary TEXT,
    ADD COLUMN IF NOT EXISTS label_secondary TEXT,
    ADD COLUMN IF NOT EXISTS acoustic_environment TEXT,
    ADD COLUMN IF NOT EXISTS source_name TEXT,
    ADD COLUMN IF NOT EXISTS source_url TEXT,
    ADD COLUMN IF NOT EXISTS origin_dataset_id TEXT,
    ADD COLUMN IF NOT EXISTS nlm_subsystem TEXT,
    ADD COLUMN IF NOT EXISTS nlm_priority TEXT,
    ADD COLUMN IF NOT EXISTS fold_id TEXT,
    ADD COLUMN IF NOT EXISTS training_split TEXT,
    ADD COLUMN IF NOT EXISTS locale TEXT,
    ADD COLUMN IF NOT EXISTS capture_time_utc TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS library.source (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'acoustic',
    source_url TEXT,
    license TEXT,
    nlm_subsystem TEXT,
    nlm_priority TEXT,
    sensor_type TEXT,
    acoustic_environment TEXT,
    description TEXT,
    access_level TEXT,
    format TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS library.detector (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'acoustic',
    upstream_project TEXT,
    upstream_url TEXT,
    description TEXT NOT NULL,
    method TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1.0',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    requires_gpu BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS library.analysis_run (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blob_id UUID NOT NULL REFERENCES library.blob(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'running',
    detectors_requested TEXT[] NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    error_message TEXT,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    visualisation JSONB,
    UNIQUE (blob_id, started_at)
);

CREATE INDEX IF NOT EXISTS idx_library_analysis_blob ON library.analysis_run(blob_id);
CREATE INDEX IF NOT EXISTS idx_library_analysis_status ON library.analysis_run(status);

CREATE TABLE IF NOT EXISTS library.detection_event (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id UUID NOT NULL REFERENCES library.analysis_run(id) ON DELETE CASCADE,
    blob_id UUID NOT NULL REFERENCES library.blob(id) ON DELETE CASCADE,
    detector_id TEXT NOT NULL REFERENCES library.detector(id),
    label TEXT NOT NULL,
    confidence DOUBLE PRECISION,
    start_sec DOUBLE PRECISION,
    end_sec DOUBLE PRECISION,
    frequency_hz DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_library_detection_blob ON library.detection_event(blob_id);
CREATE INDEX IF NOT EXISTS idx_library_detection_detector ON library.detection_event(detector_id);
CREATE INDEX IF NOT EXISTS idx_library_detection_label ON library.detection_event(label);

GRANT SELECT, INSERT, UPDATE, DELETE ON library.detector TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON library.analysis_run TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON library.detection_event TO mindex;
