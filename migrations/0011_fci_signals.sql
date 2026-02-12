-- Migration: 0011_fci_signals.sql
-- Date: February 10, 2026
-- Purpose: FCI (Fungal Computer Interface) signal storage and pattern analysis
-- 
-- This migration creates the schema for storing bioelectric signals from
-- FCI devices, detected patterns based on GFST (Global Fungi Symbiosis Theory),
-- and semantic interpretations from the Mycorrhizae Protocol.
--
-- Physics basis:
-- - Ion channel dynamics (K+, Ca2+, Na+) drive membrane potentials
-- - Signal amplitudes typically 0.1-100 µV
-- - Frequency range 0.001 Hz (circadian) to 50 Hz (fast activity)
-- - Action potential-like spikes: 2-50 mV, 0.5-5 ms duration

-- ============================================================================
-- FCI DEVICES
-- ============================================================================

CREATE TABLE IF NOT EXISTS fci_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id VARCHAR(64) UNIQUE NOT NULL,           -- MycoBrain device ID
    device_serial VARCHAR(64),                        -- Hardware serial
    device_name VARCHAR(128),
    probe_type VARCHAR(32) DEFAULT 'type_a',         -- type_a, type_b, type_c, type_d, custom
    electrode_materials JSONB DEFAULT '[]',           -- ["copper", "steel", "silver", ...]
    firmware_version VARCHAR(32),
    
    -- Location
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    altitude_m DOUBLE PRECISION,
    location_name VARCHAR(256),
    
    -- Configuration
    sample_rate_hz INTEGER DEFAULT 128,
    channels_count INTEGER DEFAULT 2,
    adc_resolution_bits INTEGER DEFAULT 16,
    
    -- Status
    status VARCHAR(32) DEFAULT 'active',              -- active, offline, maintenance, error
    last_seen TIMESTAMPTZ,
    total_readings BIGINT DEFAULT 0,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_fci_devices_device_id ON fci_devices(device_id);
CREATE INDEX idx_fci_devices_status ON fci_devices(status);
CREATE INDEX idx_fci_devices_location ON fci_devices USING gist (
    ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
) WHERE latitude IS NOT NULL AND longitude IS NOT NULL;

-- ============================================================================
-- FCI CHANNELS
-- ============================================================================

CREATE TABLE IF NOT EXISTS fci_channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID REFERENCES fci_devices(id) ON DELETE CASCADE,
    channel_index INTEGER NOT NULL,
    channel_name VARCHAR(64),
    
    -- Electrode configuration
    electrode_material VARCHAR(32),                   -- copper, silver, platinum, carbon_fiber
    electrode_diameter_mm DOUBLE PRECISION,
    electrode_spacing_mm DOUBLE PRECISION,
    
    -- Signal range
    min_amplitude_uv DOUBLE PRECISION DEFAULT -1000,
    max_amplitude_uv DOUBLE PRECISION DEFAULT 1000,
    
    -- Calibration
    calibration_offset_uv DOUBLE PRECISION DEFAULT 0,
    calibration_gain DOUBLE PRECISION DEFAULT 1.0,
    last_calibrated TIMESTAMPTZ,
    
    -- Status
    is_active BOOLEAN DEFAULT true,
    quality_score DOUBLE PRECISION DEFAULT 1.0,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    
    UNIQUE(device_id, channel_index)
);

CREATE INDEX idx_fci_channels_device ON fci_channels(device_id);

-- ============================================================================
-- FCI READINGS (raw telemetry)
-- ============================================================================

-- Partitioned by month for performance
CREATE TABLE IF NOT EXISTS fci_readings (
    id UUID DEFAULT gen_random_uuid(),
    device_id UUID NOT NULL,                          -- FK to fci_devices
    channel_id UUID,                                  -- FK to fci_channels (optional)
    
    -- Timestamp
    timestamp TIMESTAMPTZ NOT NULL,
    
    -- Bioelectric features (from firmware/signal processing)
    amplitude_uv DOUBLE PRECISION NOT NULL,
    rms_uv DOUBLE PRECISION,
    mean_uv DOUBLE PRECISION,
    std_uv DOUBLE PRECISION,
    
    -- Spectral features
    dominant_freq_hz DOUBLE PRECISION,
    spectral_centroid_hz DOUBLE PRECISION,
    total_power DOUBLE PRECISION,
    band_power_ultra_low DOUBLE PRECISION,            -- 0.01-0.1 Hz
    band_power_low DOUBLE PRECISION,                  -- 0.1-1 Hz
    band_power_mid DOUBLE PRECISION,                  -- 1-10 Hz
    band_power_high DOUBLE PRECISION,                 -- 10-50 Hz
    
    -- Quality metrics
    snr_db DOUBLE PRECISION,
    quality_score DOUBLE PRECISION,
    
    -- Spike detection
    spike_count INTEGER DEFAULT 0,
    spike_rate_hz DOUBLE PRECISION DEFAULT 0,
    
    -- Raw spectrum (optional, for detailed analysis)
    fft_magnitudes DOUBLE PRECISION[],                -- FFT magnitude array
    
    -- Envelope metadata
    envelope_id UUID,                                 -- Reference to mycorrhizae envelope
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- Create partitions for 2026
CREATE TABLE IF NOT EXISTS fci_readings_2026_01 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_02 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_03 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_04 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_05 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_06 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_07 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_08 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_09 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_10 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_11 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE IF NOT EXISTS fci_readings_2026_12 PARTITION OF fci_readings
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

CREATE INDEX idx_fci_readings_device ON fci_readings(device_id, timestamp DESC);
CREATE INDEX idx_fci_readings_channel ON fci_readings(channel_id, timestamp DESC);
CREATE INDEX idx_fci_readings_amplitude ON fci_readings(amplitude_uv) WHERE amplitude_uv > 1.0;
CREATE INDEX idx_fci_readings_spikes ON fci_readings(spike_count) WHERE spike_count > 0;

-- ============================================================================
-- FCI PATTERNS (detected GFST patterns)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fci_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID REFERENCES fci_devices(id) ON DELETE CASCADE,
    channel_id UUID REFERENCES fci_channels(id),
    
    -- Pattern identification
    pattern_name VARCHAR(64) NOT NULL,                -- baseline, active_growth, stress, etc.
    pattern_category VARCHAR(32) NOT NULL,            -- metabolic, environmental, communication, etc.
    
    -- Temporal info
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    duration_ms DOUBLE PRECISION,
    
    -- Confidence
    confidence_score DOUBLE PRECISION NOT NULL,       -- 0-1
    confidence_level VARCHAR(16),                     -- certain, high, moderate, low, speculative
    
    -- Features that triggered pattern
    amplitude_uv DOUBLE PRECISION,
    dominant_freq_hz DOUBLE PRECISION,
    spike_count INTEGER,
    spike_rate_hz DOUBLE PRECISION,
    
    -- Feature match scores (from semantic translator)
    feature_scores JSONB DEFAULT '{}',
    
    -- Phase tracking
    phase VARCHAR(16) DEFAULT 'onset',                -- onset, sustained, peak, declining, terminated
    
    -- Environmental context at time of detection
    temperature_c DOUBLE PRECISION,
    humidity_pct DOUBLE PRECISION,
    pressure_hpa DOUBLE PRECISION,
    voc_index INTEGER,
    
    -- Semantic interpretation
    interpretation_meaning TEXT,
    interpretation_implications JSONB DEFAULT '[]',
    interpretation_actions JSONB DEFAULT '[]',
    
    -- For GFST research/validation
    is_validated BOOLEAN DEFAULT false,
    validation_notes TEXT,
    validated_by VARCHAR(128),
    validated_at TIMESTAMPTZ,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_fci_patterns_device ON fci_patterns(device_id, start_time DESC);
CREATE INDEX idx_fci_patterns_name ON fci_patterns(pattern_name);
CREATE INDEX idx_fci_patterns_category ON fci_patterns(pattern_category);
CREATE INDEX idx_fci_patterns_confidence ON fci_patterns(confidence_score DESC);
CREATE INDEX idx_fci_patterns_unvalidated ON fci_patterns(is_validated) WHERE is_validated = false;
CREATE INDEX idx_fci_patterns_time ON fci_patterns(start_time DESC);

-- ============================================================================
-- GFST PATTERN LIBRARY (reference patterns)
-- ============================================================================

CREATE TABLE IF NOT EXISTS gfst_pattern_library (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Pattern definition
    name VARCHAR(64) UNIQUE NOT NULL,
    category VARCHAR(32) NOT NULL,
    version INTEGER DEFAULT 1,
    
    -- Bioelectric characteristics
    amplitude_min_uv DOUBLE PRECISION NOT NULL,
    amplitude_max_uv DOUBLE PRECISION NOT NULL,
    frequency_min_hz DOUBLE PRECISION NOT NULL,
    frequency_max_hz DOUBLE PRECISION NOT NULL,
    typical_duration_min_s DOUBLE PRECISION,
    typical_duration_max_s DOUBLE PRECISION,
    
    -- Detection thresholds
    min_snr_db DOUBLE PRECISION DEFAULT 5.0,
    min_quality DOUBLE PRECISION DEFAULT 0.3,
    requires_spikes BOOLEAN DEFAULT false,
    spike_rate_min_hz DOUBLE PRECISION,
    spike_rate_max_hz DOUBLE PRECISION,
    
    -- Spectral characteristics
    dominant_band VARCHAR(16),                        -- ultra_low, low, mid, high
    band_power_ratios JSONB,                          -- {"low": [0.4, 0.7], "mid": [0.2, 0.4]}
    
    -- Environmental correlation
    env_correlations JSONB,                           -- {"temperature": "optimal", "humidity": "high"}
    
    -- Semantic meaning
    meaning TEXT NOT NULL,
    implications JSONB DEFAULT '[]',
    recommended_actions JSONB DEFAULT '[]',
    
    -- Physics/biology basis
    physics_basis TEXT,
    biology_basis TEXT,
    references JSONB DEFAULT '[]',                    -- Literature references
    
    -- Status
    is_active BOOLEAN DEFAULT true,
    is_experimental BOOLEAN DEFAULT false,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by VARCHAR(128)
);

CREATE INDEX idx_gfst_patterns_name ON gfst_pattern_library(name);
CREATE INDEX idx_gfst_patterns_category ON gfst_pattern_library(category);
CREATE INDEX idx_gfst_patterns_active ON gfst_pattern_library(is_active);

-- ============================================================================
-- FCI STIMULATION EVENTS (bi-directional communication)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fci_stimulations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID REFERENCES fci_devices(id) ON DELETE CASCADE,
    
    -- Command info
    command_type VARCHAR(32) NOT NULL,                -- start_stimulus, stop_stimulus, calibrate
    waveform VARCHAR(32) NOT NULL,                    -- pulse, sine, square, custom
    
    -- Parameters
    amplitude_uv DOUBLE PRECISION NOT NULL,
    frequency_hz DOUBLE PRECISION,
    duration_ms INTEGER NOT NULL,
    
    -- Safety
    max_amplitude_uv DOUBLE PRECISION,
    max_duration_ms INTEGER,
    
    -- Execution
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    executed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    
    -- Result
    status VARCHAR(16) DEFAULT 'pending',             -- pending, executing, completed, failed, rejected
    result JSONB DEFAULT '{}',
    error_message TEXT,
    
    -- Source
    requested_by VARCHAR(128),                        -- user, hpl_program, autonomous
    hpl_program_id VARCHAR(128),
    
    -- Response analysis (signal changes after stimulation)
    response_amplitude_change_uv DOUBLE PRECISION,
    response_frequency_change_hz DOUBLE PRECISION,
    response_pattern_detected VARCHAR(64),
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_fci_stimulations_device ON fci_stimulations(device_id, requested_at DESC);
CREATE INDEX idx_fci_stimulations_status ON fci_stimulations(status);

-- ============================================================================
-- MYCORRHIZAE PROTOCOL ENVELOPES (message archive)
-- ============================================================================

CREATE TABLE IF NOT EXISTS mycorrhizae_envelopes (
    id UUID PRIMARY KEY,                              -- From envelope.id
    version VARCHAR(16) NOT NULL,                     -- Protocol version
    
    -- Routing
    channel VARCHAR(256) NOT NULL,
    message_type VARCHAR(32) NOT NULL,
    
    -- Source
    source_type VARCHAR(32) NOT NULL,
    source_device_id VARCHAR(64),
    source_probe_type VARCHAR(32),
    source_firmware VARCHAR(32),
    source_latitude DOUBLE PRECISION,
    source_longitude DOUBLE PRECISION,
    
    -- Timestamps
    timestamp TIMESTAMPTZ NOT NULL,
    expires TIMESTAMPTZ,
    ttl_seconds INTEGER,
    
    -- Security
    signature_algorithm VARCHAR(16),
    signature_public_key TEXT,
    signature_value TEXT,
    is_signature_valid BOOLEAN,
    
    -- Payload (stored as JSONB for flexibility)
    payload JSONB NOT NULL,
    payload_size_bytes INTEGER,
    
    -- Processing status
    received_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    processing_status VARCHAR(16) DEFAULT 'received', -- received, processing, processed, error
    processing_error TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_mycorrhizae_envelopes_device ON mycorrhizae_envelopes(source_device_id, timestamp DESC);
CREATE INDEX idx_mycorrhizae_envelopes_type ON mycorrhizae_envelopes(message_type);
CREATE INDEX idx_mycorrhizae_envelopes_channel ON mycorrhizae_envelopes(channel);
CREATE INDEX idx_mycorrhizae_envelopes_time ON mycorrhizae_envelopes(timestamp DESC);

-- ============================================================================
-- FCI NETWORK CORRELATION (multi-device signal correlation)
-- ============================================================================

CREATE TABLE IF NOT EXISTS fci_network_correlations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Devices involved
    device_a_id UUID REFERENCES fci_devices(id),
    device_b_id UUID REFERENCES fci_devices(id),
    
    -- Time window
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    
    -- Correlation metrics
    correlation_coefficient DOUBLE PRECISION NOT NULL, -- -1 to 1
    lag_ms DOUBLE PRECISION,                          -- Time lag for max correlation
    propagation_velocity_mm_min DOUBLE PRECISION,     -- If devices have known positions
    
    -- Significance
    p_value DOUBLE PRECISION,
    is_significant BOOLEAN DEFAULT false,
    
    -- Pattern context
    pattern_type VARCHAR(64),
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_fci_correlations_devices ON fci_network_correlations(device_a_id, device_b_id);
CREATE INDEX idx_fci_correlations_time ON fci_network_correlations(start_time DESC);
CREATE INDEX idx_fci_correlations_significant ON fci_network_correlations(is_significant) WHERE is_significant = true;

-- ============================================================================
-- VECTOR STORAGE FOR SIGNAL EMBEDDINGS (for ML/similarity search)
-- ============================================================================

-- Enable pgvector extension (if not already enabled)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS fci_signal_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reading_id UUID,                                  -- Reference to fci_readings
    pattern_id UUID REFERENCES fci_patterns(id),
    
    -- Embedding vector (768 dimensions typical for signal embeddings)
    embedding vector(768),
    
    -- Model info
    model_name VARCHAR(64) NOT NULL,
    model_version VARCHAR(32),
    
    -- Features used to generate embedding
    feature_summary JSONB DEFAULT '{}',
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_fci_embeddings_pattern ON fci_signal_embeddings(pattern_id);
CREATE INDEX idx_fci_embeddings_vector ON fci_signal_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ============================================================================
-- SEED GFST PATTERN LIBRARY
-- ============================================================================

INSERT INTO gfst_pattern_library (name, category, amplitude_min_uv, amplitude_max_uv, frequency_min_hz, frequency_max_hz, typical_duration_min_s, typical_duration_max_s, min_snr_db, dominant_band, meaning, implications, recommended_actions, physics_basis, biology_basis)
VALUES 
    ('baseline', 'metabolic', 0.1, 0.3, 0.01, 0.5, 60, NULL, 3.0, 'ultra_low', 
     'Normal resting state with minimal activity', 
     '["System stable", "No stress detected"]'::jsonb,
     '["Continue monitoring"]'::jsonb,
     'Minimal ion channel activity, near-equilibrium membrane potential',
     'Resting mycelium with balanced homeostasis'),
     
    ('active_growth', 'metabolic', 0.5, 2.0, 0.1, 5.0, 300, 7200, 8.0, 'low',
     'Active hyphal growth and colonization',
     '["Nutrient availability good", "Environmental conditions favorable", "Network expansion active"]'::jsonb,
     '["Document growth rate", "Monitor nutrient levels", "Consider time-lapse imaging"]'::jsonb,
     'Increased K+ channel activity drives tip growth; Ca2+ gradients guide direction',
     'Hyphal tip extension rate 1-10 µm/min; branching frequency correlated with signal variability'),
     
    ('nutrient_seeking', 'metabolic', 0.3, 1.5, 0.5, 3.0, 60, 600, 6.0, 'low',
     'Exploratory behavior seeking nutrients',
     '["Current substrate partially depleted", "Chemotaxis response active"]'::jsonb,
     '["Consider nutrient supplementation", "Monitor substrate moisture"]'::jsonb,
     'Chemoreceptor activation triggers oscillating Ca2+ signals',
     'Negative tropism away from depleted zones; positive tropism toward nutrient gradients'),
     
    ('temperature_stress', 'environmental', 1.0, 5.0, 2.0, 15.0, 30, 3600, 10.0, 'mid',
     'Thermal stress response',
     '["Temperature outside optimal range", "Potential heat shock protein activation", "Risk of growth inhibition"]'::jsonb,
     '["Adjust ambient temperature", "Check heating/cooling systems", "Alert operator"]'::jsonb,
     'Temperature affects membrane fluidity and ion channel kinetics; stress increases electrical noise',
     'Heat shock proteins (HSPs) induced; metabolic rate altered; possible sporulation trigger'),
     
    ('network_communication', 'communication', 0.5, 3.0, 0.2, 2.0, 5, 60, 8.0, 'low',
     'Inter-hyphal signaling detected',
     '["Information propagating through network", "Potential resource allocation", "Coordinated response possible"]'::jsonb,
     '["Monitor multiple channels", "Track signal propagation", "Correlate with environmental events"]'::jsonb,
     'Action potential-like spikes propagate via voltage-gated channels; speed 0.5-50 mm/min',
     'Mycorrhizal networks transfer nutrients and information between connected plants (Simard 2018)'),
     
    ('action_potential', 'communication', 2.0, 50.0, 0.5, 5.0, 0.5, 5, 15.0, 'mid',
     'Discrete action potential-like spike',
     '["Rapid signal transmission", "Ion channel activation", "Possible response to acute stimulus"]'::jsonb,
     '["Record waveform", "Correlate with stimuli", "Store for pattern learning"]'::jsonb,
     'All-or-none response; Na+/K+ channel dynamics; refractory period 50-200 ms',
     'Fungal action potentials documented by Olsson & Hansson (1995); Adamatzky (2018) computational substrate'),
     
    ('seismic_precursor', 'predictive', 0.1, 1.0, 0.001, 0.1, 3600, 86400, 5.0, 'ultra_low',
     'Potential seismic precursor signal (GFST hypothesis)',
     '["Ultra-low frequency oscillation detected", "Possible piezoelectric response to tectonic stress", "REQUIRES VALIDATION - experimental"]'::jsonb,
     '["Log for correlation with seismic data", "Do not generate public alerts", "Archive for GFST research"]'::jsonb,
     'Piezoelectric effects in minerals; stress-induced ion migration; infrasound resonance',
     'GFST hypothesis: extensive mycelial networks may integrate micro-scale geological stress signals; requires validation')
     
ON CONFLICT (name) DO UPDATE SET
    updated_at = NOW(),
    meaning = EXCLUDED.meaning,
    implications = EXCLUDED.implications;

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- Function to get recent patterns for a device
CREATE OR REPLACE FUNCTION get_device_recent_patterns(
    p_device_id UUID,
    p_hours INTEGER DEFAULT 24
)
RETURNS TABLE (
    pattern_name VARCHAR(64),
    pattern_category VARCHAR(32),
    occurrence_count BIGINT,
    avg_confidence DOUBLE PRECISION,
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        fp.pattern_name,
        fp.pattern_category,
        COUNT(*) as occurrence_count,
        AVG(fp.confidence_score) as avg_confidence,
        MIN(fp.start_time) as first_seen,
        MAX(fp.start_time) as last_seen
    FROM fci_patterns fp
    WHERE fp.device_id = p_device_id
      AND fp.start_time > NOW() - (p_hours || ' hours')::INTERVAL
    GROUP BY fp.pattern_name, fp.pattern_category
    ORDER BY occurrence_count DESC;
END;
$$ LANGUAGE plpgsql;

-- Function to calculate signal quality over time
CREATE OR REPLACE FUNCTION calculate_signal_quality_trend(
    p_device_id UUID,
    p_hours INTEGER DEFAULT 24
)
RETURNS TABLE (
    hour_bucket TIMESTAMPTZ,
    avg_snr DOUBLE PRECISION,
    avg_quality DOUBLE PRECISION,
    reading_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        date_trunc('hour', fr.timestamp) as hour_bucket,
        AVG(fr.snr_db) as avg_snr,
        AVG(fr.quality_score) as avg_quality,
        COUNT(*) as reading_count
    FROM fci_readings fr
    WHERE fr.device_id = p_device_id
      AND fr.timestamp > NOW() - (p_hours || ' hours')::INTERVAL
    GROUP BY date_trunc('hour', fr.timestamp)
    ORDER BY hour_bucket DESC;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- GRANTS
-- ============================================================================

-- Grant access to the API user
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mindex_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mindex_api;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO mindex_api;

COMMENT ON TABLE fci_devices IS 'FCI (Fungal Computer Interface) devices - MycoBrain bioelectric sensors';
COMMENT ON TABLE fci_readings IS 'Time-series bioelectric signal readings from FCI devices';
COMMENT ON TABLE fci_patterns IS 'Detected GFST patterns from signal analysis';
COMMENT ON TABLE gfst_pattern_library IS 'Reference library of GFST (Global Fungi Symbiosis Theory) patterns';
COMMENT ON TABLE mycorrhizae_envelopes IS 'Archive of Mycorrhizae Protocol message envelopes';
COMMENT ON TABLE fci_stimulations IS 'Bi-directional stimulation events sent to mycelium';
