-- Petri simulation tables - February 20, 2026
-- Session metadata, time-series metrics, calibration results, and experiment outcomes.

BEGIN;

-- =============================================================================
-- petri_simulation_sessions - Session metadata
-- =============================================================================
CREATE TABLE IF NOT EXISTS petri_simulation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(64) UNIQUE NOT NULL,
    width INT NOT NULL,
    height INT NOT NULL,
    agar_type VARCHAR(64),
    species_ids TEXT[],
    contaminant_ids TEXT[],
    virtual_hours INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_petri_sessions_session_id
    ON petri_simulation_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_petri_sessions_created
    ON petri_simulation_sessions(created_at DESC);

-- =============================================================================
-- petri_simulation_metrics - Time-series metrics per session
-- =============================================================================
CREATE TABLE IF NOT EXISTS petri_simulation_metrics (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    virtual_hour INT NOT NULL,
    sample_count INT,
    contaminant_count INT,
    total_branches INT,
    avg_nutrient DOUBLE PRECISION,
    compound_means JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- FK added only if referenced table exists (sessions created first)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'petri_simulation_sessions') THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'petri_simulation_metrics_session_fkey'
        ) THEN
            ALTER TABLE petri_simulation_metrics
                ADD CONSTRAINT petri_simulation_metrics_session_fkey
                FOREIGN KEY (session_id) REFERENCES petri_simulation_sessions(session_id) ON DELETE CASCADE;
        END IF;
    END IF;
EXCEPTION WHEN OTHERS THEN
    -- Ignore if FK already exists or table order issue
    NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_petri_metrics_session
    ON petri_simulation_metrics(session_id);
CREATE INDEX IF NOT EXISTS idx_petri_metrics_virtual_hour
    ON petri_simulation_metrics(session_id, virtual_hour);

-- =============================================================================
-- petri_calibration_results - Calibration job results
-- =============================================================================
CREATE TABLE IF NOT EXISTS petri_calibration_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    species_name VARCHAR(128) NOT NULL,
    initial_params JSONB DEFAULT '{}'::jsonb,
    calibrated_params JSONB DEFAULT '{}'::jsonb,
    bounds JSONB DEFAULT '{}'::jsonb,
    sample_count INT,
    delta_summary JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_petri_calibration_species
    ON petri_calibration_results(species_name);
CREATE INDEX IF NOT EXISTS idx_petri_calibration_created
    ON petri_calibration_results(created_at DESC);

-- =============================================================================
-- petri_experiment_outcomes - Experiment outcomes for analytics and NLM
-- =============================================================================
CREATE TABLE IF NOT EXISTS petri_experiment_outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(64),
    species_id VARCHAR(64),
    outcome_type VARCHAR(32),
    summary JSONB DEFAULT '{}'::jsonb,
    metrics_snapshot JSONB DEFAULT '{}'::jsonb,
    nlm_consumed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_petri_outcomes_session
    ON petri_experiment_outcomes(session_id);
CREATE INDEX IF NOT EXISTS idx_petri_outcomes_type
    ON petri_experiment_outcomes(outcome_type);
CREATE INDEX IF NOT EXISTS idx_petri_outcomes_nlm_consumed
    ON petri_experiment_outcomes(nlm_consumed) WHERE NOT nlm_consumed;

COMMIT;
