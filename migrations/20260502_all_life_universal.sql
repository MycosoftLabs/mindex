-- ============================================================================
-- ALL-LIFE ANCESTRY / UNIVERSAL TAXONOMY — May 2, 2026
-- Run after 20260315_earth_scale_domains.sql (species.* schema must exist).
-- Expands core.taxon for all kingdoms; relationships, media video/audio, publications link.
-- ============================================================================
BEGIN;

-- --------------------------------------------------------------------------
-- core.taxon: cross-kingdom query fields (lineage + external_id rollup)
-- --------------------------------------------------------------------------
ALTER TABLE core.taxon
    ADD COLUMN IF NOT EXISTS kingdom TEXT
        CHECK (kingdom IS NULL OR kingdom IN (
            'Fungi', 'Plantae', 'Animalia', 'Bacteria', 'Archaea',
            'Protista', 'Viruses', 'Undesignated'
        ));

ALTER TABLE core.taxon
    ADD COLUMN IF NOT EXISTS lineage TEXT[] DEFAULT NULL;

ALTER TABLE core.taxon
    ADD COLUMN IF NOT EXISTS lineage_ids UUID[] DEFAULT NULL;

ALTER TABLE core.taxon
    ADD COLUMN IF NOT EXISTS external_ids JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'core' AND table_name = 'taxon' AND column_name = 'author'
    ) THEN
        ALTER TABLE core.taxon ADD COLUMN author TEXT;
        UPDATE core.taxon SET author = authority WHERE author IS NULL;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_taxon_kingdom_rank ON core.taxon (kingdom, rank);
CREATE INDEX IF NOT EXISTS idx_taxon_kingdom_canon_lower
    ON core.taxon (kingdom, lower(canonical_name));
CREATE INDEX IF NOT EXISTS idx_taxon_lineage_gin ON core.taxon USING GIN (lineage);
CREATE INDEX IF NOT EXISTS idx_taxon_external_ids_gin ON core.taxon USING GIN (external_ids);
CREATE INDEX IF NOT EXISTS idx_taxon_metadata_gin ON core.taxon USING GIN (metadata);

COMMENT ON COLUMN core.taxon.kingdom IS 'High-level kingdom: Fungi, Plantae, Animalia, Bacteria, Archaea, Protista, Viruses, Undesignated';
COMMENT ON COLUMN core.taxon.lineage IS 'Materialized canonical names from root to self (inclusive)';
COMMENT ON COLUMN core.taxon.lineage_ids IS 'UUID chain from root to self (inclusive), parallel to lineage';
COMMENT ON COLUMN core.taxon.external_ids IS 'Rolled-up crosswalk: gbif, ncbi, inat, col, itis, eol, bold, worms, wikidata, ott, etc.';

ALTER TABLE core.taxon
    ADD COLUMN IF NOT EXISTS fungi_type TEXT;

DO $$ BEGIN
    CREATE TYPE bio.interaction_kind AS ENUM (
        'eats', 'eaten_by', 'parasite_of', 'host_of', 'mutualist_with',
        'pollinator_of', 'pollinated_by', 'mycorrhizal_with', 'decomposer_of',
        'vector_of', 'preys_on', 'symbiont_of', 'competes_with', 'commensal_with', 'other'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS bio.taxon_interaction (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_taxon_id UUID NOT NULL REFERENCES core.taxon(id) ON DELETE CASCADE,
    target_taxon_id UUID NOT NULL REFERENCES core.taxon(id) ON DELETE CASCADE,
    interaction_type bio.interaction_kind NOT NULL DEFAULT 'other',
    evidence_source TEXT,
    evidence_url TEXT,
    location geography(Point, 4326),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ti_source ON bio.taxon_interaction (source_taxon_id);
CREATE INDEX IF NOT EXISTS idx_ti_target ON bio.taxon_interaction (target_taxon_id);
CREATE INDEX IF NOT EXISTS idx_ti_type ON bio.taxon_interaction (interaction_type);
CREATE INDEX IF NOT EXISTS idx_ti_location ON bio.taxon_interaction USING GIST (location);

CREATE TABLE IF NOT EXISTS bio.taxon_characteristic (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id UUID NOT NULL REFERENCES core.taxon(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    value_text TEXT,
    value_num DOUBLE PRECISION,
    units TEXT,
    source TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tcx_taxon ON bio.taxon_characteristic (taxon_id);
CREATE INDEX IF NOT EXISTS idx_tcx_name ON bio.taxon_characteristic (name);

CREATE SCHEMA IF NOT EXISTS media;

-- Bootstrap minimal media.image when older migrations were skipped (taxon_full view counts rows).
CREATE TABLE IF NOT EXISTS media.image (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mindex_id VARCHAR(50) UNIQUE NOT NULL DEFAULT ('MYCO-IMG-' || replace(gen_random_uuid()::text, '-', '')),
    filename VARCHAR(500) NOT NULL DEFAULT '_migration_bootstrap',
    taxon_id UUID REFERENCES core.taxon(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS media.video (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id UUID REFERENCES core.taxon(id) ON DELETE SET NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT,
    license TEXT,
    attribution TEXT,
    duration_s DOUBLE PRECISION,
    width INT,
    height INT,
    embedding_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_media_video_taxon ON media.video (taxon_id);
CREATE INDEX IF NOT EXISTS idx_media_video_source ON media.video (source);

CREATE TABLE IF NOT EXISTS media.audio (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id UUID REFERENCES core.taxon(id) ON DELETE SET NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT,
    license TEXT,
    attribution TEXT,
    duration_s DOUBLE PRECISION,
    embedding_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_media_audio_taxon ON media.audio (taxon_id);
CREATE INDEX IF NOT EXISTS idx_media_audio_source ON media.audio (source);

CREATE TABLE IF NOT EXISTS bio.publication_taxon (
    publication_id VARCHAR(64) NOT NULL REFERENCES core.publications(id) ON DELETE CASCADE,
    taxon_id UUID NOT NULL REFERENCES core.taxon(id) ON DELETE CASCADE,
    relevance_score REAL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (publication_id, taxon_id)
);
CREATE INDEX IF NOT EXISTS idx_pubtax_taxon ON bio.publication_taxon (taxon_id);

-- Bootstrap compound link tables when 0007_compounds.sql was not applied (taxon_full counts use taxon_compound).
CREATE TABLE IF NOT EXISTS bio.compound (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL DEFAULT '_migration_bootstrap',
    source TEXT NOT NULL DEFAULT 'migration_bootstrap'
);

CREATE TABLE IF NOT EXISTS bio.taxon_compound (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    taxon_id UUID NOT NULL REFERENCES core.taxon(id) ON DELETE CASCADE,
    compound_id UUID NOT NULL REFERENCES bio.compound(id) ON DELETE CASCADE,
    relationship_type TEXT DEFAULT 'produces',
    evidence_level TEXT DEFAULT 'reported',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (taxon_id, compound_id)
);

CREATE INDEX IF NOT EXISTS idx_taxon_compound_taxon ON bio.taxon_compound (taxon_id);

-- species.organisms from 20260315 — link to canonical taxon
ALTER TABLE species.organisms
    ADD COLUMN IF NOT EXISTS mindex_taxon_id UUID REFERENCES core.taxon(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_organisms_mindex_taxon ON species.organisms (mindex_taxon_id);
COMMENT ON TABLE species.organisms IS
    'Supplemental earth-scale species rows (GBIF-style). core.taxon is canonical; set mindex_taxon_id to link.';

CREATE OR REPLACE VIEW bio.taxon_full AS
SELECT
    t.id,
    t.canonical_name,
    t.rank,
    t.common_name,
    t.authority,
    t.author,
    t.description,
    t.source,
    t.metadata,
    t.fungi_type,
    t.kingdom,
    t.lineage,
    t.lineage_ids,
    t.external_ids,
    t.created_at,
    t.updated_at,
    (SELECT COUNT(*)::bigint FROM obs.observation o WHERE o.taxon_id = t.id) AS obs_count,
    (SELECT COUNT(*)::bigint FROM media.image i WHERE i.taxon_id = t.id) AS image_count,
    (SELECT COUNT(*)::bigint FROM media.video v WHERE v.taxon_id = t.id) AS video_count,
    (SELECT COUNT(*)::bigint FROM media.audio a WHERE a.taxon_id = t.id) AS audio_count,
    (SELECT COUNT(*)::bigint FROM bio.genome g WHERE g.taxon_id = t.id) AS genome_count,
    (SELECT COUNT(*)::bigint FROM bio.taxon_compound tc WHERE tc.taxon_id = t.id) AS compound_link_count,
    (SELECT COUNT(*)::bigint
     FROM bio.taxon_interaction ti
     WHERE ti.source_taxon_id = t.id OR ti.target_taxon_id = t.id) AS interaction_count,
    (SELECT COUNT(*)::bigint FROM bio.publication_taxon pt WHERE pt.taxon_id = t.id) AS publication_count,
    (SELECT COUNT(*)::bigint FROM bio.taxon_characteristic c WHERE c.taxon_id = t.id) AS characteristic_count
FROM core.taxon t;

CREATE OR REPLACE VIEW bio.kingdom_stats AS
SELECT
    COALESCE(kingdom, 'Undesignated') AS kingdom,
    COUNT(*)::bigint AS taxon_count
FROM core.taxon
GROUP BY kingdom;

COMMIT;
