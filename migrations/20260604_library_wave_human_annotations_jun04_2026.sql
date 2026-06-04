-- SINE player wave editor + human identification persistence (Jun 4, 2026)

CREATE TABLE IF NOT EXISTS library.acoustic_wave_annotation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blob_id UUID NOT NULL REFERENCES library.blob(id) ON DELETE CASCADE,
    analysis_run_id UUID NULL REFERENCES library.analysis_run(id) ON DELETE SET NULL,
    selection JSONB NULL,
    zoom JSONB NULL,
    markers JSONB NOT NULL DEFAULT '[]'::jsonb,
    loop_enabled BOOLEAN NOT NULL DEFAULT false,
    reverse_enabled BOOLEAN NOT NULL DEFAULT false,
    playback_rate NUMERIC NOT NULL DEFAULT 1,
    file_context JSONB NULL,
    created_by TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_library_wave_annotation_blob
    ON library.acoustic_wave_annotation(blob_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS library.acoustic_human_identification (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    blob_id UUID NOT NULL REFERENCES library.blob(id) ON DELETE CASCADE,
    analysis_run_id UUID NULL REFERENCES library.analysis_run(id) ON DELETE SET NULL,
    human_label TEXT NOT NULL,
    human_category TEXT NULL,
    human_confidence NUMERIC NULL,
    human_notes TEXT NULL,
    disputes_model BOOLEAN NOT NULL DEFAULT true,
    model_top_label TEXT NULL,
    model_confidence NUMERIC NULL,
    model_summary JSONB NULL,
    event_context JSONB NULL,
    file_context JSONB NULL,
    review_status TEXT NOT NULL DEFAULT 'human_tagged_pending_model_review',
    training_eligible BOOLEAN NOT NULL DEFAULT true,
    created_by TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_library_human_id_blob
    ON library.acoustic_human_identification(blob_id, created_at DESC);
