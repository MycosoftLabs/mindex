-- NatureOS tools tables migration - February 19, 2026
-- Adds simulation results, spore observations, and digital twin state storage.

BEGIN;

-- =============================================================================
-- natureos_simulations - Simulation results storage
-- =============================================================================
CREATE TABLE IF NOT EXISTS natureos_simulations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    simulation_type TEXT NOT NULL,
    parameters JSONB DEFAULT '{}'::jsonb,
    results JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    user_id UUID
);

CREATE INDEX IF NOT EXISTS idx_natureos_simulations_type
    ON natureos_simulations(simulation_type);
CREATE INDEX IF NOT EXISTS idx_natureos_simulations_created
    ON natureos_simulations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_natureos_simulations_user
    ON natureos_simulations(user_id);

-- =============================================================================
-- spore_observations - Spore dispersal tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS spore_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    species_id UUID REFERENCES taxa(id),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    dispersal_vector JSONB DEFAULT '{}'::jsonb,
    observed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_spore_observations_species
    ON spore_observations(species_id);
CREATE INDEX IF NOT EXISTS idx_spore_observations_observed
    ON spore_observations(observed_at DESC);

-- =============================================================================
-- digital_twin_states - Device digital twin state snapshots
-- =============================================================================
CREATE TABLE IF NOT EXISTS digital_twin_states (
    device_id TEXT PRIMARY KEY,
    current_state JSONB DEFAULT '{}'::jsonb,
    sensor_readings JSONB DEFAULT '{}'::jsonb,
    last_sync TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_digital_twin_states_last_sync
    ON digital_twin_states(last_sync DESC);

-- =============================================================================
-- Grant permissions (adjust role as needed)
-- =============================================================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mycosoft') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON natureos_simulations TO mycosoft;
        GRANT SELECT, INSERT, UPDATE, DELETE ON spore_observations TO mycosoft;
        GRANT SELECT, INSERT, UPDATE, DELETE ON digital_twin_states TO mycosoft;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mycosoft;
    END IF;
END $$;

COMMIT;

DO $$
BEGIN
    RAISE NOTICE 'NatureOS tools migration 0014 completed successfully';
END $$;
