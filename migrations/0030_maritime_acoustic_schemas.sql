-- TAC-O Maritime Acoustic Schemas
-- Zeetachec + Mycosoft NUWC Tactical Oceanography Integration
-- Date: April 2026

CREATE TABLE IF NOT EXISTS acoustic_signatures (
    id SERIAL PRIMARY KEY,
    signature_id UUID DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT,
    frequency_range_low FLOAT,
    frequency_range_high FLOAT,
    spectral_energy JSONB,
    narrowband_peaks JSONB,
    broadband_level FLOAT,
    modulation_rate FLOAT,
    waveform_hash BYTEA,
    source TEXT,
    confidence FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_acoustic_sig_category ON acoustic_signatures(category);
CREATE INDEX IF NOT EXISTS idx_acoustic_sig_freq ON acoustic_signatures(frequency_range_low, frequency_range_high);
CREATE INDEX IF NOT EXISTS idx_acoustic_sig_uuid ON acoustic_signatures(signature_id);

CREATE TABLE IF NOT EXISTS ocean_environments (
    id SERIAL PRIMARY KEY,
    observation_id UUID DEFAULT gen_random_uuid(),
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    depth_m FLOAT,
    sound_speed FLOAT,
    temperature_c FLOAT,
    salinity_psu FLOAT,
    sea_state INT,
    current_speed FLOAT,
    current_direction FLOAT,
    bottom_depth FLOAT,
    bottom_type TEXT,
    sound_speed_profile JSONB,
    ambient_noise_spectrum JSONB,
    observed_at TIMESTAMPTZ NOT NULL,
    source TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ocean_env_location ON ocean_environments USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_ocean_env_time ON ocean_environments(observed_at);

CREATE TABLE IF NOT EXISTS magnetic_baselines (
    id SERIAL PRIMARY KEY,
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    bx FLOAT, by FLOAT, bz FLOAT,
    total_field FLOAT,
    inclination FLOAT,
    declination FLOAT,
    survey_date TIMESTAMPTZ,
    source TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mag_baseline_location ON magnetic_baselines USING GIST(location);

CREATE TABLE IF NOT EXISTS taco_observations (
    id SERIAL PRIMARY KEY,
    observation_id UUID DEFAULT gen_random_uuid(),
    sensor_id TEXT NOT NULL,
    sensor_type TEXT NOT NULL,
    location GEOGRAPHY(POINT, 4326),
    depth_m FLOAT,
    raw_data JSONB,
    processed_fingerprint JSONB,
    nlm_classification JSONB,
    anomaly_score FLOAT,
    confidence FLOAT,
    avani_review TEXT,
    observed_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    merkle_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_taco_obs_sensor ON taco_observations(sensor_id);
CREATE INDEX IF NOT EXISTS idx_taco_obs_time ON taco_observations(observed_at);
CREATE INDEX IF NOT EXISTS idx_taco_obs_location ON taco_observations USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_taco_obs_uuid ON taco_observations(observation_id);

CREATE TABLE IF NOT EXISTS taco_assessments (
    id SERIAL PRIMARY KEY,
    assessment_id UUID DEFAULT gen_random_uuid(),
    observation_ids UUID[],
    assessment_type TEXT NOT NULL,
    classification JSONB,
    recommendation JSONB,
    sonar_performance JSONB,
    urgency FLOAT,
    avani_ecological_check JSONB,
    operator_action_taken TEXT,
    assessed_at TIMESTAMPTZ DEFAULT NOW(),
    merkle_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_taco_assess_uuid ON taco_assessments(assessment_id);
CREATE INDEX IF NOT EXISTS idx_taco_assess_type ON taco_assessments(assessment_type);
CREATE INDEX IF NOT EXISTS idx_taco_assess_time ON taco_assessments(assessed_at);
