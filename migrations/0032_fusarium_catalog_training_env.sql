-- FUSARIUM additive defense catalog/training/environment schemas
-- APR 10 2026

CREATE SCHEMA IF NOT EXISTS fusarium_catalog;
CREATE SCHEMA IF NOT EXISTS fusarium_training;
CREATE SCHEMA IF NOT EXISTS fusarium_env;
CREATE SCHEMA IF NOT EXISTS fusarium_access;

CREATE TABLE IF NOT EXISTS fusarium_catalog.modality_silo (
    silo_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    source_categories JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fusarium_env.environment_domain (
    env_id TEXT PRIMARY KEY,
    axis_type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    parent_id TEXT REFERENCES fusarium_env.environment_domain(env_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fusarium_catalog.dataset_source (
    dataset_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    section_category TEXT NOT NULL,
    source_url TEXT,
    source_urls JSONB DEFAULT '[]'::jsonb,
    dataset_type TEXT,
    file_format TEXT,
    size_estimate TEXT,
    access_level TEXT,
    nlm_target TEXT,
    repo_targets JSONB DEFAULT '[]'::jsonb,
    priority TEXT,
    modality_silo TEXT REFERENCES fusarium_catalog.modality_silo(silo_id) ON DELETE SET NULL,
    license TEXT,
    description TEXT,
    environment_domain_tags JSONB DEFAULT '[]'::jsonb,
    storage_uri TEXT,
    checksum TEXT,
    ingest_status TEXT NOT NULL DEFAULT 'cataloged',
    last_verified_at TIMESTAMPTZ,
    raw_metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fusarium_dataset_priority
    ON fusarium_catalog.dataset_source(priority);

CREATE INDEX IF NOT EXISTS idx_fusarium_dataset_silo
    ON fusarium_catalog.dataset_source(modality_silo);

CREATE TABLE IF NOT EXISTS fusarium_catalog.dataset_environment_tag (
    dataset_id TEXT NOT NULL REFERENCES fusarium_catalog.dataset_source(dataset_id) ON DELETE CASCADE,
    env_id TEXT NOT NULL REFERENCES fusarium_env.environment_domain(env_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (dataset_id, env_id)
);

CREATE TABLE IF NOT EXISTS fusarium_training.dataset_manifest (
    manifest_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id TEXT NOT NULL REFERENCES fusarium_catalog.dataset_source(dataset_id) ON DELETE CASCADE,
    split_name TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    storage_uri TEXT NOT NULL,
    checksum TEXT,
    manifest_metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fusarium_manifest_dataset
    ON fusarium_training.dataset_manifest(dataset_id);

CREATE TABLE IF NOT EXISTS fusarium_training.model_registry (
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    role TEXT NOT NULL,
    source_uri TEXT,
    source_datasets JSONB DEFAULT '[]'::jsonb,
    metrics JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (model_name, model_version)
);

CREATE TABLE IF NOT EXISTS fusarium_training.training_run (
    training_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_run_ref TEXT,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registered',
    source_mix JSONB DEFAULT '[]'::jsonb,
    environment_scope JSONB DEFAULT '[]'::jsonb,
    metrics JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fusarium_access.data_promotion_rule (
    rule_id TEXT PRIMARY KEY,
    source_plane TEXT NOT NULL,
    target_plane TEXT NOT NULL,
    direction TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS fusarium_access.data_promotion_audit (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id TEXT,
    source_plane TEXT NOT NULL,
    target_plane TEXT NOT NULL,
    promoted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT,
    details JSONB DEFAULT '{}'::jsonb
);
