-- MYCA2 PSILO — mutation lineage, eval cases, artifacts, alias history, rollback audit, PSILO journal (Mar 17, 2026)

CREATE TABLE IF NOT EXISTS plasticity.mutation_run (
    mutation_run_id VARCHAR(128) PRIMARY KEY,
    candidate_id VARCHAR(128) REFERENCES plasticity.model_candidate(candidate_id) ON DELETE SET NULL,
    parent_mutation_run_id VARCHAR(128),
    operators_applied JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_summary JSONB
);

CREATE TABLE IF NOT EXISTS plasticity.lineage_event (
    event_id VARCHAR(128) PRIMARY KEY,
    candidate_id VARCHAR(128) REFERENCES plasticity.model_candidate(candidate_id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plasticity.eval_case_result (
    id SERIAL PRIMARY KEY,
    eval_run_id VARCHAR(128) NOT NULL REFERENCES plasticity.eval_run(eval_run_id) ON DELETE CASCADE,
    case_id VARCHAR(256) NOT NULL,
    passed BOOLEAN,
    score DOUBLE PRECISION,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plasticity.artifact_meta (
    artifact_id VARCHAR(128) PRIMARY KEY,
    candidate_id VARCHAR(128) REFERENCES plasticity.model_candidate(candidate_id) ON DELETE SET NULL,
    uri TEXT NOT NULL,
    content_hash VARCHAR(128),
    kind VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS plasticity.alias_history (
    id SERIAL PRIMARY KEY,
    alias VARCHAR(128) NOT NULL,
    from_candidate_id VARCHAR(128),
    to_candidate_id VARCHAR(128) NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason VARCHAR(256)
);

CREATE TABLE IF NOT EXISTS plasticity.rollback_event (
    rollback_id VARCHAR(128) PRIMARY KEY,
    alias VARCHAR(128) NOT NULL,
    from_candidate_id VARCHAR(128),
    to_candidate_id VARCHAR(128) NOT NULL,
    decided_by VARCHAR(256),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plasticity.psilo_session (
    session_id VARCHAR(128) PRIMARY KEY,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    dose_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    phase_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    overlay_edges JSONB NOT NULL DEFAULT '[]'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    integration_report JSONB
);

CREATE TABLE IF NOT EXISTS plasticity.psilo_session_event (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL REFERENCES plasticity.psilo_session(session_id) ON DELETE CASCADE,
    event_type VARCHAR(128) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_psilo_session_status ON plasticity.psilo_session(status);
CREATE INDEX IF NOT EXISTS idx_lineage_candidate ON plasticity.lineage_event(candidate_id);
CREATE INDEX IF NOT EXISTS idx_eval_case_run ON plasticity.eval_case_result(eval_run_id);
CREATE INDEX IF NOT EXISTS idx_alias_history_alias ON plasticity.alias_history(alias);
