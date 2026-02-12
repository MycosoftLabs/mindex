-- Migration: 0012_genetics.sql
-- Description: Add genetic_sequences table for GenBank/NCBI sequence data
-- Author: Mycosoft Genetics Integration
-- Date: 2026-02-10

BEGIN;

-- ============================================================================
-- GENETIC SEQUENCES TABLE
-- Stores DNA/RNA sequences from GenBank, NCBI, and other sources
-- ============================================================================

CREATE TABLE IF NOT EXISTS bio.genetic_sequence (
    id SERIAL PRIMARY KEY,
    
    -- Unique accession number (e.g., AF123456, NC_001144)
    accession VARCHAR(50) NOT NULL UNIQUE,
    
    -- Link to taxon (optional - not all sequences have known taxa)
    taxon_id UUID REFERENCES core.taxon (id) ON DELETE SET NULL,
    
    -- Species name (cached for quick access even without taxon link)
    species_name VARCHAR(255),
    
    -- Gene/region information
    gene VARCHAR(100),          -- e.g., ITS, LSU, SSU, RPB1, RPB2, TEF1
    region VARCHAR(100),        -- e.g., ITS1, ITS2, D1/D2
    
    -- The actual sequence data
    sequence TEXT NOT NULL,
    sequence_length INTEGER NOT NULL,
    
    -- Sequence type
    sequence_type VARCHAR(20) DEFAULT 'dna',  -- dna, rna, protein
    
    -- Source information
    source VARCHAR(50) NOT NULL DEFAULT 'genbank',  -- genbank, ncbi, bold, unite
    source_url TEXT,
    
    -- Additional identifiers
    gi_number BIGINT,           -- GenBank GI number (legacy)
    version VARCHAR(20),        -- Accession version (e.g., AF123456.1)
    
    -- Quality/annotation metadata
    definition TEXT,            -- GenBank definition line
    organism TEXT,              -- Organism as listed in GenBank
    taxonomy TEXT,              -- Full taxonomy string
    
    -- Publication reference
    pubmed_id INTEGER,
    doi TEXT,
    authors TEXT,
    title TEXT,
    journal TEXT,
    publication_date DATE,
    
    -- Metadata (for additional fields)
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Primary lookups
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_accession ON bio.genetic_sequence (accession);
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_taxon ON bio.genetic_sequence (taxon_id);
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_species ON bio.genetic_sequence (species_name);

-- Gene/region filtering
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_gene ON bio.genetic_sequence (gene);
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_region ON bio.genetic_sequence (region);

-- Source filtering
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_source ON bio.genetic_sequence (source);

-- Sequence length range queries
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_length ON bio.genetic_sequence (sequence_length);

-- Full-text search on species name
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_species_trgm ON bio.genetic_sequence 
    USING gin (species_name gin_trgm_ops);

-- Full-text search on gene
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_gene_trgm ON bio.genetic_sequence 
    USING gin (gene gin_trgm_ops);

-- Combined index for common queries
CREATE INDEX IF NOT EXISTS idx_genetic_sequence_gene_species ON bio.genetic_sequence (gene, species_name);

-- ============================================================================
-- SEQUENCE FEATURES TABLE
-- Stores annotated features within sequences (CDS, rRNA, etc.)
-- ============================================================================

CREATE TABLE IF NOT EXISTS bio.sequence_feature (
    id SERIAL PRIMARY KEY,
    
    sequence_id INTEGER NOT NULL REFERENCES bio.genetic_sequence (id) ON DELETE CASCADE,
    
    -- Feature type (e.g., CDS, rRNA, gene, misc_feature)
    feature_type VARCHAR(50) NOT NULL,
    
    -- Location within the sequence
    start_pos INTEGER NOT NULL,
    end_pos INTEGER NOT NULL,
    strand CHAR(1) DEFAULT '+',  -- '+' or '-'
    
    -- Feature annotation
    gene_name VARCHAR(100),
    product TEXT,
    note TEXT,
    
    -- Metadata
    qualifiers JSONB NOT NULL DEFAULT '{}'::jsonb,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sequence_feature_sequence ON bio.sequence_feature (sequence_id);
CREATE INDEX IF NOT EXISTS idx_sequence_feature_type ON bio.sequence_feature (feature_type);
CREATE INDEX IF NOT EXISTS idx_sequence_feature_gene ON bio.sequence_feature (gene_name);

-- ============================================================================
-- PRIMER SEQUENCES TABLE
-- Stores PCR primers used for amplification
-- ============================================================================

CREATE TABLE IF NOT EXISTS bio.primer_sequence (
    id SERIAL PRIMARY KEY,
    
    -- Primer identification
    name VARCHAR(100) NOT NULL UNIQUE,
    
    -- Primer sequence
    sequence VARCHAR(100) NOT NULL,
    sequence_length INTEGER NOT NULL,
    
    -- Target gene/region
    target_gene VARCHAR(100),
    target_region VARCHAR(100),
    
    -- Direction
    direction VARCHAR(10) NOT NULL DEFAULT 'forward',  -- forward, reverse
    
    -- Physical properties
    tm_celsius DECIMAL(5,2),      -- Melting temperature
    gc_percent DECIMAL(5,2),      -- GC content percentage
    
    -- Reference
    reference TEXT,
    doi TEXT,
    
    -- Metadata
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_primer_target_gene ON bio.primer_sequence (target_gene);
CREATE INDEX IF NOT EXISTS idx_primer_name ON bio.primer_sequence (name);

-- ============================================================================
-- SEED COMMON FUNGAL PRIMERS
-- ============================================================================

INSERT INTO bio.primer_sequence (name, sequence, sequence_length, target_gene, target_region, direction, reference) VALUES
    ('ITS1', 'TCCGTAGGTGAACCTGCGG', 19, 'ITS', 'ITS1', 'forward', 'White et al. 1990'),
    ('ITS4', 'TCCTCCGCTTATTGATATGC', 20, 'ITS', 'ITS2', 'reverse', 'White et al. 1990'),
    ('ITS5', 'GGAAGTAAAAGTCGTAACAAGG', 22, 'ITS', 'ITS1', 'forward', 'White et al. 1990'),
    ('LR0R', 'ACCCGCTGAACTTAAGC', 17, 'LSU', 'D1', 'forward', 'Vilgalys Lab'),
    ('LR5', 'TCCTGAGGGAAACTTCG', 17, 'LSU', 'D2', 'reverse', 'Vilgalys & Hester 1990'),
    ('NS1', 'GTAGTCATATGCTTGTCTC', 19, 'SSU', 'SSU', 'forward', 'White et al. 1990'),
    ('NS4', 'CTTCCGTCAATTCCTTTAAG', 20, 'SSU', 'SSU', 'reverse', 'White et al. 1990'),
    ('EF1-983F', 'GCYCCYGGHCAYCGTGAYTTYAT', 23, 'TEF1', 'TEF1', 'forward', 'Rehner & Buckley 2005'),
    ('EF1-2218R', 'ATGACACCRACRGCRACRGTYTG', 23, 'TEF1', 'TEF1', 'reverse', 'Rehner & Buckley 2005'),
    ('RPB1-Af', 'GARTGYCCDGGDCAYTTYGG', 20, 'RPB1', 'RPB1', 'forward', 'Matheny et al. 2002'),
    ('RPB1-Cr', 'CCNGCDATNTCRTTRTCCATRTA', 23, 'RPB1', 'RPB1', 'reverse', 'Matheny et al. 2002'),
    ('fRPB2-5F', 'GAYGAYMGWGATCAYTTYGG', 20, 'RPB2', 'RPB2', 'forward', 'Liu et al. 1999'),
    ('fRPB2-7cR', 'CCCATRGCTTGTYYRCCCAT', 20, 'RPB2', 'RPB2', 'reverse', 'Liu et al. 1999')
ON CONFLICT (name) DO NOTHING;

-- ============================================================================
-- HELPER VIEW: Sequences with taxon info
-- ============================================================================

CREATE OR REPLACE VIEW app.v_genetic_sequence_with_taxon AS
SELECT
    gs.id,
    gs.accession,
    gs.taxon_id,
    gs.species_name,
    gs.gene,
    gs.region,
    gs.sequence,
    gs.sequence_length,
    gs.sequence_type,
    gs.source,
    gs.source_url,
    gs.definition,
    gs.organism,
    gs.pubmed_id,
    gs.doi,
    gs.created_at,
    gs.updated_at,
    t.canonical_name AS taxon_canonical_name,
    t.common_name AS taxon_common_name,
    t.rank AS taxon_rank
FROM bio.genetic_sequence gs
LEFT JOIN core.taxon t ON t.id = gs.taxon_id;

-- ============================================================================
-- HELPER VIEW: Sequence statistics by gene
-- ============================================================================

CREATE OR REPLACE VIEW app.v_sequence_stats_by_gene AS
SELECT
    gene,
    COUNT(*) AS sequence_count,
    COUNT(DISTINCT species_name) AS species_count,
    AVG(sequence_length)::INTEGER AS avg_length,
    MIN(sequence_length) AS min_length,
    MAX(sequence_length) AS max_length
FROM bio.genetic_sequence
WHERE gene IS NOT NULL
GROUP BY gene
ORDER BY sequence_count DESC;

-- ============================================================================
-- TRIGGER: Auto-update updated_at timestamp
-- ============================================================================

CREATE OR REPLACE FUNCTION bio.update_genetic_sequence_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_genetic_sequence_updated ON bio.genetic_sequence;
CREATE TRIGGER trg_genetic_sequence_updated
    BEFORE UPDATE ON bio.genetic_sequence
    FOR EACH ROW
    EXECUTE FUNCTION bio.update_genetic_sequence_timestamp();

-- ============================================================================
-- MIGRATION LOG
-- ============================================================================

INSERT INTO core.migration_log (name, applied_at) 
VALUES ('0012_genetics', now())
ON CONFLICT DO NOTHING;

COMMIT;
