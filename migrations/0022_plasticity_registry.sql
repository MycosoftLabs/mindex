-- Plasticity Forge Phase 1 — registry for model evolution (Mar 14, 2026)
-- Candidate genome, training/eval/promotion records, runtime alias state.
-- MINDEX is the source of truth for lineage and rollback; no mock data.

CREATE SCHEMA IF NOT EXISTS plasticity;

-- Model candidate (genome): first-class record per evolution branch
CREATE TABLE IF NOT EXISTS plasticity.model_candidate (
    candidate_id VARCHAR(128) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Lineage
    parent_candidate_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    base_model_id VARCHAR(256),
    artifact_uri TEXT,

    -- Mutation
    mutation_operators_applied JSONB NOT NULL DEFAULT '[]'::jsonb,
    data_curriculum_hash VARCHAR(128),
    training_code_hash VARCHAR(128),

    -- Eval and safety
    eval_suite_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    eval_summary JSONB,
    safety_verdict VARCHAR(64),

    -- Hardware envelope
    latency_p50_ms DOUBLE PRECISION,
    latency_p99_ms DOUBLE PRECISION,
    memory_mb DOUBLE PRECISION,
    watts DOUBLE PRECISION,
    jetson_compatible BOOLEAN NOT NULL DEFAULT FALSE,

    -- Lifecycle and rollback
    lifecycle VARCHAR(32) NOT NULL DEFAULT 'shadow',
    rollback_target_candidate_id VARCHAR(128) REFERENCES plasticity.model_candidate(candidate_id) ON DELETE SET NULL,
    promoted_at TIMESTAMPTZ,
    alias VARCHAR(128)
);

-- Training run linked to a candidate (optional link to nlm.training_runs via config)
CREATE TABLE IF NOT EXISTS plasticity.training_run (
    run_id VARCHAR(128) PRIMARY KEY,
    candidate_id VARCHAR(128) NOT NULL REFERENCES plasticity.model_candidate(candidate_id) ON DELETE CASCADE,
    nlm_run_id VARCHAR(64),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Eval run: one execution of an eval suite for a candidate
CREATE TABLE IF NOT EXISTS plasticity.eval_run (
    eval_run_id VARCHAR(128) PRIMARY KEY,
    candidate_id VARCHAR(128) NOT NULL REFERENCES plasticity.model_candidate(candidate_id) ON DELETE CASCADE,
    suite_id VARCHAR(128) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    results JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Promotion decision: audit record for shadow/canary -> active or rollback
CREATE TABLE IF NOT EXISTS plasticity.promotion_decision (
    decision_id VARCHAR(128) PRIMARY KEY,
    candidate_id VARCHAR(128) NOT NULL REFERENCES plasticity.model_candidate(candidate_id) ON DELETE CASCADE,
    from_lifecycle VARCHAR(32) NOT NULL,
    to_lifecycle VARCHAR(32) NOT NULL,
    alias VARCHAR(128),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    policy_id VARCHAR(128),
    decided_by VARCHAR(256)
);

-- Runtime alias state: live alias -> candidate_id for registry-backed resolver
CREATE TABLE IF NOT EXISTS plasticity.runtime_alias_state (
    alias VARCHAR(128) PRIMARY KEY,
    candidate_id VARCHAR(128) NOT NULL REFERENCES plasticity.model_candidate(candidate_id) ON DELETE CASCADE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plasticity_candidate_lifecycle ON plasticity.model_candidate(lifecycle);
CREATE INDEX IF NOT EXISTS idx_plasticity_candidate_parent ON plasticity.model_candidate USING GIN (parent_candidate_ids);
CREATE INDEX IF NOT EXISTS idx_plasticity_training_candidate ON plasticity.training_run(candidate_id);
CREATE INDEX IF NOT EXISTS idx_plasticity_eval_candidate ON plasticity.eval_run(candidate_id);
CREATE INDEX IF NOT EXISTS idx_plasticity_promotion_candidate ON plasticity.promotion_decision(candidate_id);
