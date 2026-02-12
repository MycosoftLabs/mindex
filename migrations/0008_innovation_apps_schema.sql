-- Migration: 0008_innovation_apps_schema.sql
-- Purpose: Create complete schema for Innovation Apps data storage
-- Date: 2026-01-24
-- Author: Mycosoft Engineering

-- ============================================
-- NLM SCHEMA - Nature Learning Model Data
-- ============================================

CREATE SCHEMA IF NOT EXISTS nlm;

-- User Sessions (tracks all app usage)
CREATE TABLE IF NOT EXISTS nlm.user_session (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    app_name VARCHAR(50) NOT NULL,
    session_start TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_end TIMESTAMPTZ,
    events JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_session_user ON nlm.user_session(user_id);
CREATE INDEX IF NOT EXISTS idx_user_session_app ON nlm.user_session(app_name);
CREATE INDEX IF NOT EXISTS idx_user_session_start ON nlm.user_session(session_start);

-- ============================================
-- PHYSICS SIMULATOR TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS nlm.physics_simulation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    session_id UUID REFERENCES nlm.user_session(id),
    compound_id UUID REFERENCES bio.compound(id),
    molecule_name VARCHAR(100) NOT NULL,
    method VARCHAR(20) NOT NULL CHECK (method IN ('qise', 'md', 'tensor')),
    parameters JSONB NOT NULL,
    results JSONB NOT NULL,
    execution_time_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_physics_sim_user ON nlm.physics_simulation(user_id);
CREATE INDEX IF NOT EXISTS idx_physics_sim_compound ON nlm.physics_simulation(compound_id);
CREATE INDEX IF NOT EXISTS idx_physics_sim_method ON nlm.physics_simulation(method);

CREATE TABLE IF NOT EXISTS nlm.field_observation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    session_id UUID REFERENCES nlm.user_session(id),
    location GEOGRAPHY(POINT, 4326),
    altitude FLOAT,
    observation_time TIMESTAMPTZ NOT NULL,
    geomagnetic JSONB,
    lunar JSONB,
    atmospheric JSONB,
    fruiting_prediction JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_field_obs_user ON nlm.field_observation(user_id);
CREATE INDEX IF NOT EXISTS idx_field_obs_location ON nlm.field_observation USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_field_obs_time ON nlm.field_observation(observation_time);

-- ============================================
-- DIGITAL TWIN TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS nlm.digital_twin (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    device_id VARCHAR(20),
    species_id UUID REFERENCES core.taxon(id),
    name VARCHAR(100) NOT NULL,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    network_topology JSONB,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_twin_user ON nlm.digital_twin(user_id);
CREATE INDEX IF NOT EXISTS idx_twin_device ON nlm.digital_twin(device_id);
CREATE INDEX IF NOT EXISTS idx_twin_species ON nlm.digital_twin(species_id);
CREATE INDEX IF NOT EXISTS idx_twin_active ON nlm.digital_twin(is_active);

CREATE TABLE IF NOT EXISTS nlm.twin_snapshot (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    twin_id UUID NOT NULL REFERENCES nlm.digital_twin(id) ON DELETE CASCADE,
    state JSONB NOT NULL,
    sensor_data JSONB,
    network_metrics JSONB,
    snapshot_time TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_snapshot_twin ON nlm.twin_snapshot(twin_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_time ON nlm.twin_snapshot(snapshot_time);

CREATE TABLE IF NOT EXISTS nlm.twin_prediction (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    twin_id UUID NOT NULL REFERENCES nlm.digital_twin(id) ON DELETE CASCADE,
    prediction_window_hours INTEGER NOT NULL,
    predicted_biomass FLOAT,
    predicted_density FLOAT,
    fruiting_probability FLOAT,
    recommendations JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prediction_twin ON nlm.twin_prediction(twin_id);

-- ============================================
-- LIFECYCLE SIMULATOR TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS bio.species_lifecycle_profile (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id UUID REFERENCES core.taxon(id) UNIQUE,
    germination_temp_optimal FLOAT,
    germination_temp_min FLOAT,
    germination_temp_max FLOAT,
    fruiting_temp_optimal FLOAT,
    fruiting_temp_min FLOAT,
    fruiting_temp_max FLOAT,
    humidity_optimal FLOAT,
    humidity_min FLOAT,
    co2_optimal_ppm INTEGER,
    light_hours_optimal INTEGER,
    germination_days_min INTEGER,
    germination_days_max INTEGER,
    mycelium_days_min INTEGER,
    mycelium_days_max INTEGER,
    fruiting_days_min INTEGER,
    fruiting_days_max INTEGER,
    total_cycle_days_typical INTEGER,
    growth_rate_mm_per_day FLOAT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_profile_taxon ON bio.species_lifecycle_profile(taxon_id);

CREATE TABLE IF NOT EXISTS nlm.lifecycle_simulation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    session_id UUID REFERENCES nlm.user_session(id),
    species_id UUID REFERENCES core.taxon(id),
    initial_conditions JSONB NOT NULL,
    current_stage VARCHAR(30),
    current_progress FLOAT,
    day_count INTEGER DEFAULT 0,
    biomass FLOAT,
    health FLOAT,
    final_state JSONB,
    completed BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_sim_user ON nlm.lifecycle_simulation(user_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_sim_species ON nlm.lifecycle_simulation(species_id);

CREATE TABLE IF NOT EXISTS nlm.lifecycle_stage_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    simulation_id UUID NOT NULL REFERENCES nlm.lifecycle_simulation(id) ON DELETE CASCADE,
    stage VARCHAR(30) NOT NULL,
    progress FLOAT,
    conditions JSONB,
    health FLOAT,
    biomass FLOAT,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stage_log_sim ON nlm.lifecycle_stage_log(simulation_id);

-- ============================================
-- GENETIC CIRCUIT TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS bio.genetic_circuit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    species_id UUID REFERENCES core.taxon(id),
    description TEXT,
    pathway_type VARCHAR(50),
    target_compound_id UUID REFERENCES bio.compound(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_circuit_species ON bio.genetic_circuit(species_id);
CREATE INDEX IF NOT EXISTS idx_circuit_compound ON bio.genetic_circuit(target_compound_id);

CREATE TABLE IF NOT EXISTS bio.circuit_component (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    circuit_id UUID NOT NULL REFERENCES bio.genetic_circuit(id) ON DELETE CASCADE,
    name VARCHAR(50) NOT NULL,
    component_type VARCHAR(20) NOT NULL CHECK (component_type IN ('gene', 'protein', 'metabolite', 'enzyme')),
    position INTEGER,
    initial_expression FLOAT DEFAULT 50,
    color VARCHAR(7),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_component_circuit ON bio.circuit_component(circuit_id);

CREATE TABLE IF NOT EXISTS bio.circuit_interaction (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    circuit_id UUID NOT NULL REFERENCES bio.genetic_circuit(id) ON DELETE CASCADE,
    source_id UUID REFERENCES bio.circuit_component(id),
    target_id UUID REFERENCES bio.circuit_component(id),
    interaction_type VARCHAR(20) NOT NULL CHECK (interaction_type IN ('activates', 'represses', 'produces', 'catalyzes')),
    strength FLOAT DEFAULT 1.0
);

CREATE INDEX IF NOT EXISTS idx_interaction_circuit ON bio.circuit_interaction(circuit_id);

CREATE TABLE IF NOT EXISTS nlm.circuit_simulation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    session_id UUID REFERENCES nlm.user_session(id),
    circuit_id UUID REFERENCES bio.genetic_circuit(id),
    modifications JSONB,
    parameters JSONB,
    trajectory JSONB,
    final_metabolite FLOAT,
    bottleneck_gene VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_circuit_sim_user ON nlm.circuit_simulation(user_id);
CREATE INDEX IF NOT EXISTS idx_circuit_sim_circuit ON nlm.circuit_simulation(circuit_id);

-- ============================================
-- SYMBIOSIS NETWORK TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS bio.symbiosis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_taxon_id UUID REFERENCES core.taxon(id),
    target_taxon_id UUID REFERENCES core.taxon(id),
    relationship_type VARCHAR(30) NOT NULL CHECK (relationship_type IN (
        'mycorrhizal', 'parasitic', 'saprotrophic', 'endophytic', 
        'lichen', 'predatory', 'commensal', 'mutualistic'
    )),
    strength FLOAT DEFAULT 1.0,
    bidirectional BOOLEAN DEFAULT false,
    evidence_level VARCHAR(20),
    source_reference TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_symbiosis_source ON bio.symbiosis(source_taxon_id);
CREATE INDEX IF NOT EXISTS idx_symbiosis_target ON bio.symbiosis(target_taxon_id);
CREATE INDEX IF NOT EXISTS idx_symbiosis_type ON bio.symbiosis(relationship_type);

CREATE TABLE IF NOT EXISTS bio.symbiosis_observation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbiosis_id UUID REFERENCES bio.symbiosis(id) ON DELETE CASCADE,
    location GEOGRAPHY(POINT, 4326),
    observed_at TIMESTAMPTZ,
    observer_user_id UUID,
    notes TEXT,
    images JSONB,
    verified BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_symbiosis_obs_symbiosis ON bio.symbiosis_observation(symbiosis_id);
CREATE INDEX IF NOT EXISTS idx_symbiosis_obs_location ON bio.symbiosis_observation USING GIST(location);

CREATE TABLE IF NOT EXISTS nlm.symbiosis_analysis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    session_id UUID REFERENCES nlm.user_session(id),
    filter_type VARCHAR(30),
    network_snapshot JSONB,
    statistics JSONB,
    keystone_species JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_symbiosis_analysis_user ON nlm.symbiosis_analysis(user_id);

-- ============================================
-- RETROSYNTHESIS TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS bio.biosynthetic_pathway (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    compound_id UUID REFERENCES bio.compound(id),
    species_id UUID REFERENCES core.taxon(id),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    overall_yield FLOAT,
    total_steps INTEGER,
    difficulty VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pathway_compound ON bio.biosynthetic_pathway(compound_id);
CREATE INDEX IF NOT EXISTS idx_pathway_species ON bio.biosynthetic_pathway(species_id);

CREATE TABLE IF NOT EXISTS bio.pathway_step (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pathway_id UUID NOT NULL REFERENCES bio.biosynthetic_pathway(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    substrate_name VARCHAR(100) NOT NULL,
    product_name VARCHAR(100) NOT NULL,
    enzyme_name VARCHAR(100),
    enzyme_type VARCHAR(50),
    enzyme_ec_number VARCHAR(20),
    conditions TEXT,
    yield_fraction FLOAT,
    reversible BOOLEAN DEFAULT false,
    substrate_compound_id UUID REFERENCES bio.compound(id),
    product_compound_id UUID REFERENCES bio.compound(id),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_step_pathway ON bio.pathway_step(pathway_id);
CREATE INDEX IF NOT EXISTS idx_step_number ON bio.pathway_step(step_number);

CREATE TABLE IF NOT EXISTS nlm.pathway_analysis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    session_id UUID REFERENCES nlm.user_session(id),
    compound_id UUID REFERENCES bio.compound(id),
    pathway_id UUID REFERENCES bio.biosynthetic_pathway(id),
    analysis_result JSONB,
    rate_limiting_step INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pathway_analysis_user ON nlm.pathway_analysis(user_id);
CREATE INDEX IF NOT EXISTS idx_pathway_analysis_compound ON nlm.pathway_analysis(compound_id);

-- ============================================
-- ALCHEMY LAB TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS chem.molecular_scaffold (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    smiles TEXT,
    num_positions INTEGER,
    category VARCHAR(50),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chem.functional_group (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    abbreviation VARCHAR(20),
    smiles TEXT,
    effect TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chem.designed_compound (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    session_id UUID REFERENCES nlm.user_session(id),
    name VARCHAR(100),
    scaffold_id VARCHAR(50) REFERENCES chem.molecular_scaffold(id),
    modifications JSONB NOT NULL,
    smiles TEXT,
    inchi TEXT,
    inchikey TEXT,
    molecular_weight FLOAT,
    logp FLOAT,
    drug_likeness FLOAT,
    synthesizability FLOAT,
    toxicity_risk FLOAT,
    bioactivities JSONB,
    status VARCHAR(20) DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_designed_user ON chem.designed_compound(user_id);
CREATE INDEX IF NOT EXISTS idx_designed_scaffold ON chem.designed_compound(scaffold_id);
CREATE INDEX IF NOT EXISTS idx_designed_status ON chem.designed_compound(status);

CREATE TABLE IF NOT EXISTS chem.synthesis_plan (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    compound_id UUID NOT NULL REFERENCES chem.designed_compound(id) ON DELETE CASCADE,
    steps JSONB NOT NULL,
    overall_yield FLOAT,
    estimated_cost FLOAT,
    difficulty VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_synthesis_compound ON chem.synthesis_plan(compound_id);

-- ============================================
-- USER DATA ACCESS TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS nlm.user_data_export (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    export_type VARCHAR(20) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    file_path TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_export_user ON nlm.user_data_export(user_id);
CREATE INDEX IF NOT EXISTS idx_export_status ON nlm.user_data_export(status);

CREATE TABLE IF NOT EXISTS nlm.user_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE,
    default_species_id UUID REFERENCES core.taxon(id),
    theme VARCHAR(20) DEFAULT 'dark',
    opt_out_nlm_training BOOLEAN DEFAULT false,
    notification_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    app_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_preferences_user ON nlm.user_preferences(user_id);

-- ============================================
-- NLM TRAINING DATA TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS nlm.training_batch (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_type VARCHAR(50) NOT NULL,
    source_app VARCHAR(50),
    data_count INTEGER,
    processed BOOLEAN DEFAULT false,
    model_version VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_batch_processed ON nlm.training_batch(processed);
CREATE INDEX IF NOT EXISTS idx_batch_app ON nlm.training_batch(source_app);

-- ============================================
-- CREP INTEGRATION TABLES
-- ============================================

CREATE TABLE IF NOT EXISTS crep.biological_threat (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    threat_type VARCHAR(50) NOT NULL,
    source_app VARCHAR(50),
    location GEOGRAPHY(POINT, 4326),
    severity FLOAT,
    confidence FLOAT,
    details JSONB,
    status VARCHAR(20) DEFAULT 'active',
    acknowledged_by UUID,
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_threat_type ON crep.biological_threat(threat_type);
CREATE INDEX IF NOT EXISTS idx_threat_location ON crep.biological_threat USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_threat_status ON crep.biological_threat(status);
CREATE INDEX IF NOT EXISTS idx_threat_severity ON crep.biological_threat(severity);

-- ============================================
-- SEED DATA - SCAFFOLDS & FUNCTIONAL GROUPS
-- ============================================

INSERT INTO chem.molecular_scaffold (id, name, num_positions, category, description) VALUES
('indole', 'Indole', 6, 'alkaloid', 'Bicyclic aromatic structure found in tryptamine compounds'),
('ergoline', 'Ergoline', 6, 'alkaloid', 'Tetracyclic structure found in ergot alkaloids'),
('beta-carboline', 'β-Carboline', 5, 'alkaloid', 'Tricyclic structure found in harmine-type compounds'),
('lanostane', 'Lanostane', 8, 'triterpene', 'Tetracyclic triterpene scaffold for ganoderic acids'),
('macrolide', 'Macrolide', 6, 'polyketide', 'Large lactone ring found in many bioactive compounds')
ON CONFLICT (id) DO NOTHING;

INSERT INTO chem.functional_group (id, name, abbreviation, effect) VALUES
('hydroxyl', 'Hydroxyl', '-OH', 'Increases solubility, antioxidant activity'),
('amino', 'Amino', '-NH₂', 'Increases polarity, antimicrobial activity'),
('methyl', 'Methyl', '-CH₃', 'Increases lipophilicity, metabolic stability'),
('phosphate', 'Phosphate', '-PO₄', 'Increases water solubility, prodrug potential'),
('acetyl', 'Acetyl', '-COCH₃', 'Modifies receptor binding, metabolic stability'),
('phenyl', 'Phenyl', '-C₆H₅', 'Increases lipophilicity, protein binding')
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- SEED DATA - GENETIC CIRCUITS
-- ============================================

INSERT INTO bio.genetic_circuit (id, name, description, pathway_type) VALUES
('psilocybin-pathway', 'Psilocybin Biosynthesis', 'Tryptophan to psilocybin biosynthetic pathway', 'biosynthetic'),
('hericenone-pathway', 'Hericenone Production', 'Hericium erinaceus secondary metabolite pathway', 'biosynthetic'),
('ganoderic-pathway', 'Ganoderic Acid Synthesis', 'Lanosterol to ganoderic acid pathway', 'biosynthetic'),
('cordycepin-pathway', 'Cordycepin Biosynthesis', 'Adenosine to cordycepin pathway', 'biosynthetic')
ON CONFLICT (id) DO NOTHING;

-- ============================================
-- VIEWS FOR EASY QUERYING
-- ============================================

CREATE OR REPLACE VIEW app.v_user_activity_summary AS
SELECT 
    user_id,
    app_name,
    COUNT(*) as session_count,
    SUM(EXTRACT(EPOCH FROM (COALESCE(session_end, now()) - session_start))/3600)::NUMERIC(10,2) as total_hours,
    MAX(session_start) as last_activity
FROM nlm.user_session
GROUP BY user_id, app_name;

CREATE OR REPLACE VIEW app.v_designed_compounds_with_predictions AS
SELECT 
    dc.*,
    ms.name as scaffold_name,
    sp.overall_yield as synthesis_yield,
    sp.difficulty as synthesis_difficulty
FROM chem.designed_compound dc
LEFT JOIN chem.molecular_scaffold ms ON dc.scaffold_id = ms.id
LEFT JOIN chem.synthesis_plan sp ON dc.id = sp.compound_id;

-- Grant permissions
GRANT USAGE ON SCHEMA nlm TO authenticated;
GRANT USAGE ON SCHEMA chem TO authenticated;
GRANT USAGE ON SCHEMA crep TO authenticated;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA nlm TO authenticated;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA chem TO authenticated;
GRANT SELECT ON ALL TABLES IN SCHEMA crep TO authenticated;

-- Add comments
COMMENT ON SCHEMA nlm IS 'Nature Learning Model - user session and simulation data';
COMMENT ON SCHEMA chem IS 'Chemistry - molecular design and synthesis planning';
COMMENT ON SCHEMA crep IS 'Common Relevant Environmental Picture - threat detection';
COMMENT ON TABLE nlm.user_session IS 'Tracks all user interactions across innovation apps';
COMMENT ON TABLE nlm.digital_twin IS 'Real-time digital representations of mycelial networks';
COMMENT ON TABLE chem.designed_compound IS 'User-created virtual compounds from Alchemy Lab';
