-- Rich labeling for NLM training (per-file catalog, May 27 2026)
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

CREATE INDEX IF NOT EXISTS idx_library_blob_label ON library.blob(label_primary);
CREATE INDEX IF NOT EXISTS idx_library_blob_env ON library.blob(acoustic_environment);
CREATE INDEX IF NOT EXISTS idx_library_blob_origin ON library.blob(origin_dataset_id);
CREATE INDEX IF NOT EXISTS idx_library_blob_title ON library.blob(title);

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

GRANT SELECT, INSERT, UPDATE, DELETE ON library.source TO mindex;
