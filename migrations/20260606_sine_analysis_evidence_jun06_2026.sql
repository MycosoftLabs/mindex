-- SINE acoustic analysis evidence persistence.
-- This migration creates storage for real model outputs, prototype matches,
-- fusion rows, and evidence-linked sound transcripts. It does not insert fake
-- evidence rows.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS sine;

CREATE TABLE IF NOT EXISTS sine.model_output (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id uuid NOT NULL REFERENCES library.analysis_run(id) ON DELETE CASCADE,
    blob_id uuid NOT NULL REFERENCES library.blob(id) ON DELETE CASCADE,
    model_id text REFERENCES sine.model_artifact(model_id) ON UPDATE CASCADE ON DELETE SET NULL,
    output_kind text NOT NULL DEFAULT 'classification',
    window_start_sec double precision,
    window_end_sec double precision,
    top_label text,
    confidence double precision,
    ood_score double precision,
    labels jsonb NOT NULL DEFAULT '[]'::jsonb,
    scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding_ref text,
    embedding_sha256 text,
    artifact_sha256 text,
    label_map_sha256 text,
    runtime_ms double precision,
    latency_ms double precision,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sine_model_output_run
    ON sine.model_output (analysis_run_id);

CREATE INDEX IF NOT EXISTS idx_sine_model_output_blob
    ON sine.model_output (blob_id);

CREATE INDEX IF NOT EXISTS idx_sine_model_output_model
    ON sine.model_output (model_id);

CREATE INDEX IF NOT EXISTS idx_sine_model_output_label
    ON sine.model_output (top_label);

CREATE TABLE IF NOT EXISTS sine.prototype_match (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id uuid NOT NULL REFERENCES library.analysis_run(id) ON DELETE CASCADE,
    blob_id uuid NOT NULL REFERENCES library.blob(id) ON DELETE CASCADE,
    model_output_id uuid REFERENCES sine.model_output(id) ON DELETE SET NULL,
    prototype_id text REFERENCES sine.prototype(prototype_id) ON UPDATE CASCADE ON DELETE SET NULL,
    label text,
    category text,
    source text,
    source_uri text,
    license text,
    score double precision,
    distance double precision,
    segment_start_sec double precision,
    segment_end_sec double precision,
    vector_sha256 text,
    prototype_sha256 text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sine_prototype_match_run
    ON sine.prototype_match (analysis_run_id);

CREATE INDEX IF NOT EXISTS idx_sine_prototype_match_blob
    ON sine.prototype_match (blob_id);

CREATE INDEX IF NOT EXISTS idx_sine_prototype_match_prototype
    ON sine.prototype_match (prototype_id);

CREATE TABLE IF NOT EXISTS sine.fusion_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id uuid NOT NULL REFERENCES library.analysis_run(id) ON DELETE CASCADE,
    blob_id uuid NOT NULL REFERENCES library.blob(id) ON DELETE CASCADE,
    detector_event_id uuid REFERENCES library.detection_event(id) ON DELETE SET NULL,
    model_output_id uuid REFERENCES sine.model_output(id) ON DELETE SET NULL,
    prototype_match_id uuid REFERENCES sine.prototype_match(id) ON DELETE SET NULL,
    kind text NOT NULL,
    label text,
    event_family text,
    event_type text,
    score double precision,
    weight double precision,
    detail text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sine_fusion_evidence_run
    ON sine.fusion_evidence (analysis_run_id);

CREATE INDEX IF NOT EXISTS idx_sine_fusion_evidence_blob
    ON sine.fusion_evidence (blob_id);

CREATE INDEX IF NOT EXISTS idx_sine_fusion_evidence_kind
    ON sine.fusion_evidence (kind);

CREATE TABLE IF NOT EXISTS sine.sound_transcript (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id uuid NOT NULL REFERENCES library.analysis_run(id) ON DELETE CASCADE,
    blob_id uuid NOT NULL REFERENCES library.blob(id) ON DELETE CASCADE,
    start_sec double precision NOT NULL,
    end_sec double precision NOT NULL,
    label text NOT NULL,
    description text,
    sound_source text,
    confidence double precision,
    frequency_range text,
    event_family text,
    model_output_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    fusion_evidence_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    prototype_match_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    detector_event_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    evidence_summary text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sine_sound_transcript_run
    ON sine.sound_transcript (analysis_run_id);

CREATE INDEX IF NOT EXISTS idx_sine_sound_transcript_blob_time
    ON sine.sound_transcript (blob_id, start_sec, end_sec);

GRANT SELECT, INSERT, UPDATE, DELETE ON sine.model_output TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON sine.prototype_match TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON sine.fusion_evidence TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON sine.sound_transcript TO mindex;
