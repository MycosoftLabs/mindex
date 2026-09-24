-- NLM FormSpace spine — September 23, 2026
-- Training runs, mutations, grounding, rooted frames timeline
-- SHA-256 attestation columns (mica remains BLAKE3 internally; product proofs use SHA-256)

CREATE SCHEMA IF NOT EXISTS nlm;

ALTER TABLE nlm.training_runs
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS formspace_chart_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS merkle_root CHAR(64),
    ADD COLUMN IF NOT EXISTS sha256_leaf CHAR(64),
    ADD COLUMN IF NOT EXISTS ecdsa_signature TEXT,
    ADD COLUMN IF NOT EXISTS storage_ref TEXT,
    ADD COLUMN IF NOT EXISTS model_kind VARCHAR(64) NOT NULL DEFAULT 'nature_learning_model';

CREATE TABLE IF NOT EXISTS nlm.mutations (
    mutation_id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) REFERENCES nlm.training_runs(run_id) ON DELETE CASCADE,
    mutation_type VARCHAR(32) NOT NULL,
    target_layer TEXT,
    magnitude DOUBLE PRECISION,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    epoch INTEGER,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    merkle_root CHAR(64),
    sha256_leaf CHAR(64)
);

CREATE TABLE IF NOT EXISTS nlm.change_log (
    change_id VARCHAR(64) PRIMARY KEY,
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(128) NOT NULL,
    action VARCHAR(64) NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nlm_change_log_ts ON nlm.change_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_nlm_change_log_entity ON nlm.change_log(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS nlm.grounding_records (
    grounding_id VARCHAR(64) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    record JSONB NOT NULL,
    sha256_leaf CHAR(64),
    merkle_root CHAR(64),
    ecdsa_signature TEXT,
    zk_proof JSONB,
    zk_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_nlm_grounding_created ON nlm.grounding_records(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_nlm_grounding_root ON nlm.grounding_records(merkle_root);

CREATE TABLE IF NOT EXISTS nlm.rooted_frames (
    frame_id VARCHAR(64) PRIMARY KEY,
    device_id VARCHAR(128) NOT NULL,
    sensor_id VARCHAR(128),
    protocol JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sha256 CHAR(64) NOT NULL,
    merkle_root CHAR(64),
    storage_ref TEXT NOT NULL,
    signed BOOLEAN NOT NULL DEFAULT FALSE,
    source VARCHAR(64) NOT NULL DEFAULT 'nlm_ingest',
    labels JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_nlm_rooted_frames_device_ts
    ON nlm.rooted_frames(device_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_nlm_rooted_frames_sha ON nlm.rooted_frames(sha256);

CREATE TABLE IF NOT EXISTS nlm.agent_tasks (
    task_id VARCHAR(64) PRIMARY KEY,
    task_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    requested_by VARCHAR(128),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nlm_agent_tasks_created ON nlm.agent_tasks(created_at DESC);
