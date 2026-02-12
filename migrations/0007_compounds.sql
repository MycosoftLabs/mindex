-- Migration: 0007_compounds.sql
-- Description: Add compound tables for ChemSpider integration
-- Author: Mycosoft ChemSpider Integration
-- Date: 2026-01-24

BEGIN;

-- ============================================================================
-- COMPOUND TABLES
-- Chemical compound data from ChemSpider and other chemistry databases
-- ============================================================================

-- Main compound table
CREATE TABLE IF NOT EXISTS bio.compound (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Identity
    name text NOT NULL,
    iupac_name text,
    
    -- Structure identifiers
    formula text,
    smiles text,
    inchi text,
    inchikey text UNIQUE,
    
    -- Physical properties
    molecular_weight double precision,
    monoisotopic_mass double precision,
    average_mass double precision,
    
    -- External IDs
    chemspider_id integer UNIQUE,
    pubchem_id integer,
    cas_number text,
    chebi_id text,
    
    -- Classification
    chemical_class text,
    compound_type text,  -- alkaloid, terpene, polysaccharide, etc.
    
    -- Source info
    source text NOT NULL DEFAULT 'chemspider',
    
    -- Metadata
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_compound_name ON bio.compound (name);
CREATE INDEX IF NOT EXISTS idx_compound_formula ON bio.compound (formula);
CREATE INDEX IF NOT EXISTS idx_compound_smiles ON bio.compound (smiles);
CREATE INDEX IF NOT EXISTS idx_compound_chemspider_id ON bio.compound (chemspider_id);
CREATE INDEX IF NOT EXISTS idx_compound_pubchem_id ON bio.compound (pubchem_id);
CREATE INDEX IF NOT EXISTS idx_compound_chemical_class ON bio.compound (chemical_class);
CREATE INDEX IF NOT EXISTS idx_compound_type ON bio.compound (compound_type);

-- Full-text search on compound name
CREATE INDEX IF NOT EXISTS idx_compound_name_trgm ON bio.compound 
    USING gin (name gin_trgm_ops);

-- ============================================================================
-- TAXON-COMPOUND RELATIONSHIP
-- Links compounds to species that produce/contain them
-- ============================================================================

CREATE TABLE IF NOT EXISTS bio.taxon_compound (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    taxon_id uuid NOT NULL REFERENCES core.taxon (id) ON DELETE CASCADE,
    compound_id uuid NOT NULL REFERENCES bio.compound (id) ON DELETE CASCADE,
    
    -- Relationship details
    relationship_type text DEFAULT 'produces',  -- produces, contains, metabolizes
    evidence_level text DEFAULT 'reported',  -- verified, reported, predicted
    
    -- Quantitative data (if available)
    concentration_min double precision,
    concentration_max double precision,
    concentration_unit text,  -- mg/g, %, etc.
    
    -- Tissue/part where compound is found
    tissue_location text,  -- fruiting_body, mycelium, spores, etc.
    
    -- Source citation
    source text,
    source_url text,
    doi text,
    
    -- Metadata
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    
    -- Unique constraint
    UNIQUE (taxon_id, compound_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_taxon_compound_taxon ON bio.taxon_compound (taxon_id);
CREATE INDEX IF NOT EXISTS idx_taxon_compound_compound ON bio.taxon_compound (compound_id);
CREATE INDEX IF NOT EXISTS idx_taxon_compound_type ON bio.taxon_compound (relationship_type);
CREATE INDEX IF NOT EXISTS idx_taxon_compound_evidence ON bio.taxon_compound (evidence_level);

-- ============================================================================
-- COMPOUND PROPERTIES
-- Extensible properties for compounds (biological activity, toxicity, etc.)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bio.compound_property (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    compound_id uuid NOT NULL REFERENCES bio.compound (id) ON DELETE CASCADE,
    
    -- Property details
    property_name text NOT NULL,
    property_category text,  -- biological_activity, toxicity, pharmacology, etc.
    
    -- Values (one of these should be set)
    value_text text,
    value_numeric double precision,
    value_boolean boolean,
    value_unit text,
    
    -- Source
    source text,
    source_url text,
    doi text,
    
    -- Metadata
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now(),
    
    -- Unique constraint per property per compound
    UNIQUE (compound_id, property_name)
);

CREATE INDEX IF NOT EXISTS idx_compound_property_compound ON bio.compound_property (compound_id);
CREATE INDEX IF NOT EXISTS idx_compound_property_name ON bio.compound_property (property_name);
CREATE INDEX IF NOT EXISTS idx_compound_property_category ON bio.compound_property (property_category);

-- ============================================================================
-- COMPOUND EXTERNAL REFERENCES
-- Links to external databases (PubChem, ChEBI, DrugBank, etc.)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bio.compound_external_ref (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    compound_id uuid NOT NULL REFERENCES bio.compound (id) ON DELETE CASCADE,
    
    -- External reference
    source_name text NOT NULL,  -- PubChem, ChEBI, DrugBank, KEGG, etc.
    external_id text NOT NULL,
    external_url text,
    
    -- Metadata
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now(),
    
    -- Unique constraint
    UNIQUE (compound_id, source_name)
);

CREATE INDEX IF NOT EXISTS idx_compound_ext_ref_compound ON bio.compound_external_ref (compound_id);
CREATE INDEX IF NOT EXISTS idx_compound_ext_ref_source ON bio.compound_external_ref (source_name);

-- ============================================================================
-- COMPOUND STRUCTURE IMAGES
-- Cached structure images (PNG, SVG) from ChemSpider
-- ============================================================================

CREATE TABLE IF NOT EXISTS bio.compound_image (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    compound_id uuid NOT NULL REFERENCES bio.compound (id) ON DELETE CASCADE,
    
    -- Image data
    image_type text NOT NULL DEFAULT 'png',  -- png, svg
    image_data bytea,
    image_url text,
    
    -- Metadata
    width integer,
    height integer,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now(),
    
    -- One image per type per compound
    UNIQUE (compound_id, image_type)
);

CREATE INDEX IF NOT EXISTS idx_compound_image_compound ON bio.compound_image (compound_id);

-- ============================================================================
-- BIOLOGICAL ACTIVITY LOOKUP
-- Common biological activities for quick filtering
-- ============================================================================

CREATE TABLE IF NOT EXISTS bio.biological_activity (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    name text NOT NULL UNIQUE,
    category text,  -- antimicrobial, anticancer, neuroprotective, etc.
    description text,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Seed common biological activities
INSERT INTO bio.biological_activity (name, category, description) VALUES
    ('Antibacterial', 'antimicrobial', 'Activity against bacteria'),
    ('Antifungal', 'antimicrobial', 'Activity against fungi'),
    ('Antiviral', 'antimicrobial', 'Activity against viruses'),
    ('Antiparasitic', 'antimicrobial', 'Activity against parasites'),
    ('Anticancer', 'oncology', 'Anticancer or cytotoxic activity'),
    ('Antitumor', 'oncology', 'Inhibits tumor growth'),
    ('Immunomodulating', 'immunology', 'Modulates immune system function'),
    ('Immunostimulating', 'immunology', 'Stimulates immune response'),
    ('Immunosuppressive', 'immunology', 'Suppresses immune response'),
    ('Neuroprotective', 'neurology', 'Protects neurons from damage'),
    ('Neurotrophic', 'neurology', 'Promotes neuron growth'),
    ('Psychoactive', 'neurology', 'Affects mental processes'),
    ('Antidepressant', 'psychiatry', 'Reduces depression symptoms'),
    ('Anxiolytic', 'psychiatry', 'Reduces anxiety'),
    ('Hepatoprotective', 'gastroenterology', 'Protects liver'),
    ('Cardioprotective', 'cardiology', 'Protects heart'),
    ('Anti-inflammatory', 'general', 'Reduces inflammation'),
    ('Antioxidant', 'general', 'Reduces oxidative stress'),
    ('Hypoglycemic', 'metabolism', 'Lowers blood sugar'),
    ('Cholesterol-lowering', 'metabolism', 'Reduces cholesterol'),
    ('Adaptogenic', 'general', 'Helps body adapt to stress'),
    ('Prebiotic', 'microbiome', 'Promotes beneficial gut bacteria')
ON CONFLICT (name) DO NOTHING;

-- ============================================================================
-- COMPOUND-ACTIVITY RELATIONSHIP
-- Links compounds to their biological activities
-- ============================================================================

CREATE TABLE IF NOT EXISTS bio.compound_activity (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    compound_id uuid NOT NULL REFERENCES bio.compound (id) ON DELETE CASCADE,
    activity_id uuid NOT NULL REFERENCES bio.biological_activity (id) ON DELETE CASCADE,
    
    -- Activity details
    potency text,  -- high, moderate, low
    mechanism text,
    target text,  -- receptor, enzyme, pathway
    
    -- Evidence
    evidence_level text DEFAULT 'reported',  -- in_vitro, in_vivo, clinical, reported
    source text,
    doi text,
    
    -- Metadata
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    
    -- Timestamps
    created_at timestamptz NOT NULL DEFAULT now(),
    
    -- Unique constraint
    UNIQUE (compound_id, activity_id)
);

CREATE INDEX IF NOT EXISTS idx_compound_activity_compound ON bio.compound_activity (compound_id);
CREATE INDEX IF NOT EXISTS idx_compound_activity_activity ON bio.compound_activity (activity_id);

-- ============================================================================
-- HELPER VIEWS
-- ============================================================================

-- View: Compounds with their biological activities
CREATE OR REPLACE VIEW app.v_compound_with_activities AS
SELECT
    c.id,
    c.name,
    c.formula,
    c.molecular_weight,
    c.smiles,
    c.inchikey,
    c.chemspider_id,
    c.pubchem_id,
    c.chemical_class,
    c.compound_type,
    COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'activity_id', ba.id,
                'activity_name', ba.name,
                'category', ba.category,
                'potency', ca.potency,
                'evidence_level', ca.evidence_level
            )
            ORDER BY ba.category, ba.name
        ) FILTER (WHERE ba.id IS NOT NULL),
        '[]'::jsonb
    ) AS activities
FROM bio.compound c
LEFT JOIN bio.compound_activity ca ON ca.compound_id = c.id
LEFT JOIN bio.biological_activity ba ON ba.id = ca.activity_id
GROUP BY c.id;

-- View: Taxon with associated compounds
CREATE OR REPLACE VIEW app.v_taxon_compounds AS
SELECT
    t.id AS taxon_id,
    t.canonical_name,
    t.common_name,
    t.rank,
    COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'compound_id', c.id,
                'name', c.name,
                'formula', c.formula,
                'molecular_weight', c.molecular_weight,
                'chemspider_id', c.chemspider_id,
                'relationship_type', tc.relationship_type,
                'evidence_level', tc.evidence_level,
                'tissue_location', tc.tissue_location
            )
            ORDER BY c.name
        ) FILTER (WHERE c.id IS NOT NULL),
        '[]'::jsonb
    ) AS compounds
FROM core.taxon t
LEFT JOIN bio.taxon_compound tc ON tc.taxon_id = t.id
LEFT JOIN bio.compound c ON c.id = tc.compound_id
GROUP BY t.id;

-- View: Compound search with full details
CREATE OR REPLACE VIEW app.v_compound_search AS
SELECT
    c.id,
    c.name,
    c.iupac_name,
    c.formula,
    c.molecular_weight,
    c.smiles,
    c.inchi,
    c.inchikey,
    c.chemspider_id,
    c.pubchem_id,
    c.cas_number,
    c.chemical_class,
    c.compound_type,
    c.source,
    c.metadata,
    c.created_at,
    c.updated_at,
    -- Species count
    (SELECT COUNT(*) FROM bio.taxon_compound tc WHERE tc.compound_id = c.id) AS species_count,
    -- Activity count
    (SELECT COUNT(*) FROM bio.compound_activity ca WHERE ca.compound_id = c.id) AS activity_count
FROM bio.compound c;

-- ============================================================================
-- MIGRATION LOG
-- ============================================================================

INSERT INTO core.migration_log (name, applied_at) 
VALUES ('0007_compounds', now())
ON CONFLICT DO NOTHING;

COMMIT;
