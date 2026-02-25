-- NLM Training Schema - February 17, 2026
-- Phase 2 support tables: encoders, self-supervised training, sporebase labels

CREATE SCHEMA IF NOT EXISTS nlm;

CREATE TABLE IF NOT EXISTS nlm.training_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS nlm.nature_embeddings (
    embedding_id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_id VARCHAR(128),
    packet JSONB NOT NULL,
    embedding VECTOR(16),
    anomaly_score DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS nlm.self_supervised_metrics (
    metric_id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(64) REFERENCES nlm.training_runs(run_id) ON DELETE CASCADE,
    step INTEGER NOT NULL,
    prediction_loss DOUBLE PRECISION NOT NULL,
    contrastive_loss DOUBLE PRECISION NOT NULL,
    segmentation_loss DOUBLE PRECISION NOT NULL,
    total_loss DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nlm.sporebase_labels (
    segment_id VARCHAR(128) PRIMARY KEY,
    start_utc TIMESTAMPTZ NOT NULL,
    end_utc TIMESTAMPTZ NOT NULL,
    species_name TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_nlm_embeddings_ts ON nlm.nature_embeddings(ts DESC);
CREATE INDEX IF NOT EXISTS idx_nlm_embeddings_anomaly ON nlm.nature_embeddings(anomaly_score DESC);
CREATE INDEX IF NOT EXISTS idx_nlm_metrics_run_step ON nlm.self_supervised_metrics(run_id, step);

