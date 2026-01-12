-- Migration: 0004_fungi_types.sql
-- Purpose: Add fungi_type classification and edibility/medicinal columns
-- Expected: Better categorization of mushrooms, yeasts, molds, and mildews

-- Add fungi_type column to core.taxon table
ALTER TABLE core.taxon ADD COLUMN IF NOT EXISTS fungi_type TEXT;
COMMENT ON COLUMN core.taxon.fungi_type IS 'Classification: mushroom, yeast, mold, mildew, lichen, truffle, polypore, etc.';

-- Add index for fungi_type filtering
CREATE INDEX IF NOT EXISTS idx_taxon_fungi_type ON core.taxon (fungi_type);

-- Ensure bio.trait table exists with proper structure
CREATE TABLE IF NOT EXISTS bio.trait (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id UUID NOT NULL REFERENCES core.taxon(id) ON DELETE CASCADE,
    trait_name TEXT NOT NULL,
    value_text TEXT,
    value_numeric NUMERIC,
    value_boolean BOOLEAN,
    unit TEXT,
    source TEXT,
    confidence NUMERIC(3,2) DEFAULT 1.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (taxon_id, trait_name)
);

-- Add specific columns for edibility and medicinal uses
ALTER TABLE bio.trait ADD COLUMN IF NOT EXISTS edibility TEXT;
ALTER TABLE bio.trait ADD COLUMN IF NOT EXISTS medicinal_uses TEXT[];

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_trait_taxon_id ON bio.trait (taxon_id);
CREATE INDEX IF NOT EXISTS idx_trait_name ON bio.trait (trait_name);
CREATE INDEX IF NOT EXISTS idx_trait_edibility ON bio.trait (edibility);

-- Create trigger for updated_at
CREATE OR REPLACE FUNCTION update_trait_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_trait_updated_at_trigger ON bio.trait;
CREATE TRIGGER update_trait_updated_at_trigger
BEFORE UPDATE ON bio.trait
FOR EACH ROW
EXECUTE FUNCTION update_trait_updated_at();

-- Create view for easy access to species with their primary traits
CREATE OR REPLACE VIEW core.species_with_traits AS
SELECT 
    t.id,
    t.canonical_name,
    t.rank,
    t.source,
    t.fungi_type,
    MAX(CASE WHEN tr.trait_name = 'edibility' THEN tr.value_text END) AS edibility,
    MAX(CASE WHEN tr.trait_name = 'habitat' THEN tr.value_text END) AS habitat,
    MAX(CASE WHEN tr.trait_name = 'spore_print' THEN tr.value_text END) AS spore_print,
    MAX(CASE WHEN tr.trait_name = 'cap_shape' THEN tr.value_text END) AS cap_shape,
    MAX(CASE WHEN tr.trait_name = 'pathogenic' THEN tr.value_text END) AS pathogenic,
    t.metadata,
    t.created_at,
    t.updated_at
FROM core.taxon t
LEFT JOIN bio.trait tr ON t.id = tr.taxon_id
WHERE t.rank = 'species'
GROUP BY t.id;

-- Update existing taxa with inferred fungi_type based on taxonomy
UPDATE core.taxon
SET fungi_type = 'mushroom'
WHERE fungi_type IS NULL
AND (
    canonical_name ILIKE '%mushroom%'
    OR metadata->>'order' IN ('Agaricales', 'Boletales', 'Russulales', 'Polyporales')
    OR metadata->>'class' = 'Agaricomycetes'
);

UPDATE core.taxon
SET fungi_type = 'yeast'
WHERE fungi_type IS NULL
AND (
    canonical_name ILIKE '%yeast%'
    OR metadata->>'class' IN ('Saccharomycetes', 'Tremellomycetes')
    OR source = 'theyeasts'
);

UPDATE core.taxon
SET fungi_type = 'mold'
WHERE fungi_type IS NULL
AND (
    canonical_name ILIKE '%mold%'
    OR canonical_name ILIKE '%fusarium%'
    OR canonical_name ILIKE '%aspergillus%'
    OR canonical_name ILIKE '%penicillium%'
    OR source = 'fusarium'
);

UPDATE core.taxon
SET fungi_type = 'mildew'
WHERE fungi_type IS NULL
AND (
    canonical_name ILIKE '%mildew%'
    OR metadata->>'order' = 'Erysiphales'
);

UPDATE core.taxon
SET fungi_type = 'lichen'
WHERE fungi_type IS NULL
AND (
    canonical_name ILIKE '%lichen%'
    OR metadata->>'order' IN ('Lecanorales', 'Peltigerales')
);

UPDATE core.taxon
SET fungi_type = 'truffle'
WHERE fungi_type IS NULL
AND (
    canonical_name ILIKE '%truffle%'
    OR canonical_name ILIKE '%tuber %'
    OR metadata->>'order' = 'Pezizales'
);

-- Create statistics view
CREATE OR REPLACE VIEW core.taxa_statistics AS
SELECT 
    COUNT(*) AS total_taxa,
    COUNT(DISTINCT CASE WHEN rank = 'species' THEN id END) AS species_count,
    COUNT(DISTINCT CASE WHEN rank = 'genus' THEN id END) AS genus_count,
    COUNT(DISTINCT CASE WHEN rank = 'family' THEN id END) AS family_count,
    COUNT(DISTINCT source) AS source_count,
    COUNT(DISTINCT fungi_type) AS fungi_type_count,
    COUNT(CASE WHEN fungi_type = 'mushroom' THEN 1 END) AS mushroom_count,
    COUNT(CASE WHEN fungi_type = 'yeast' THEN 1 END) AS yeast_count,
    COUNT(CASE WHEN fungi_type = 'mold' THEN 1 END) AS mold_count,
    COUNT(CASE WHEN fungi_type = 'mildew' THEN 1 END) AS mildew_count,
    COUNT(CASE WHEN fungi_type = 'lichen' THEN 1 END) AS lichen_count,
    COUNT(CASE WHEN fungi_type = 'truffle' THEN 1 END) AS truffle_count
FROM core.taxon;

-- Log migration
INSERT INTO core.migration_log (migration_name, applied_at)
VALUES ('0004_fungi_types', NOW())
ON CONFLICT (migration_name) DO NOTHING;
