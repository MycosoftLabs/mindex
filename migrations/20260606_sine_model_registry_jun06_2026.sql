-- SINE acoustic model/prototype registry.
-- This migration creates honest registry tables; it does not register fake
-- models or prototype rows.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS sine;

CREATE TABLE IF NOT EXISTS sine.model_artifact (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id text NOT NULL UNIQUE,
    model_name text NOT NULL,
    model_version text NOT NULL,
    domain text NOT NULL DEFAULT 'acoustic',
    target_domains text[] NOT NULL DEFAULT '{}',
    class_families text[] NOT NULL DEFAULT '{}',
    framework text NOT NULL,
    runtime text NOT NULL,
    artifact_uri text NOT NULL,
    artifact_sha256 text NOT NULL,
    label_map_uri text,
    label_map_sha256 text,
    training_dataset text,
    metrics_uri text,
    confusion_matrix_uri text,
    input_sample_rate_hz integer,
    window_sec numeric,
    label_count integer,
    embedding_dim integer,
    device text,
    status text NOT NULL DEFAULT 'registered',
    loaded boolean NOT NULL DEFAULT false,
    last_loaded_at timestamptz,
    last_inference_at timestamptz,
    last_error text,
    backend_commit text,
    feature_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sine_model_artifact_status
    ON sine.model_artifact (status, loaded);

CREATE INDEX IF NOT EXISTS idx_sine_model_artifact_domain
    ON sine.model_artifact (domain);

CREATE TABLE IF NOT EXISTS sine.prototype (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    prototype_id text NOT NULL UNIQUE,
    label text NOT NULL,
    domain text NOT NULL,
    category text,
    source text,
    source_uri text,
    license text,
    model_id text REFERENCES sine.model_artifact(model_id) ON UPDATE CASCADE ON DELETE SET NULL,
    embedding_dim integer,
    vector_sha256 text,
    prototype_sha256 text,
    example_count integer NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sine_prototype_domain_category
    ON sine.prototype (domain, category);

CREATE INDEX IF NOT EXISTS idx_sine_prototype_model
    ON sine.prototype (model_id);

CREATE INDEX IF NOT EXISTS idx_sine_prototype_label_trgm
    ON sine.prototype USING gin (label gin_trgm_ops);
