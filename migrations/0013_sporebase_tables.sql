-- SporeBase tables migration - February 12, 2026
-- Sample tracking, telemetry time series, and lab results for SporeBase v4 devices.

-- =============================================================================
-- sporebase_samples - Sample/tape segment tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS sporebase_samples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT NOT NULL,
    segment_number INTEGER,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    tape_position FLOAT,
    status TEXT DEFAULT 'collected',
    lab_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sporebase_samples_device 
    ON sporebase_samples(device_id);
CREATE INDEX IF NOT EXISTS idx_sporebase_samples_status 
    ON sporebase_samples(status);
CREATE INDEX IF NOT EXISTS idx_sporebase_samples_created 
    ON sporebase_samples(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sporebase_samples_start_time 
    ON sporebase_samples(start_time);

-- =============================================================================
-- sporebase_telemetry - Telemetry time series per device
-- =============================================================================
CREATE TABLE IF NOT EXISTS sporebase_telemetry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    spore_count FLOAT,
    voc_index FLOAT,
    temperature FLOAT,
    humidity FLOAT,
    pressure FLOAT,
    flow_rate FLOAT
);

CREATE INDEX IF NOT EXISTS idx_sporebase_telemetry_device 
    ON sporebase_telemetry(device_id);
CREATE INDEX IF NOT EXISTS idx_sporebase_telemetry_timestamp 
    ON sporebase_telemetry(device_id, timestamp DESC);

-- =============================================================================
-- sporebase_lab_results - Lab analysis results per sample
-- =============================================================================
CREATE TABLE IF NOT EXISTS sporebase_lab_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_id UUID REFERENCES sporebase_samples(id) ON DELETE CASCADE,
    analysis_type TEXT NOT NULL,
    results JSONB DEFAULT '{}'::jsonb,
    analyzed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sporebase_lab_results_sample 
    ON sporebase_lab_results(sample_id);
CREATE INDEX IF NOT EXISTS idx_sporebase_lab_results_analysis 
    ON sporebase_lab_results(analysis_type);

-- =============================================================================
-- Grant permissions (adjust role as needed)
-- =============================================================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mycosoft') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON sporebase_samples TO mycosoft;
        GRANT SELECT, INSERT, UPDATE, DELETE ON sporebase_telemetry TO mycosoft;
        GRANT SELECT, INSERT, UPDATE, DELETE ON sporebase_lab_results TO mycosoft;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mycosoft;
    END IF;
END $$;

DO $$
BEGIN
    RAISE NOTICE 'SporeBase tables migration 0013 completed successfully';
END $$;
