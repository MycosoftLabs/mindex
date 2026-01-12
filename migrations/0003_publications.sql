-- Publications Table Migration
-- Stores mycological research publications from various sources

-- Create publications table in core schema
CREATE TABLE IF NOT EXISTS core.publications (
    id VARCHAR(64) PRIMARY KEY,
    source VARCHAR(50) NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    title TEXT NOT NULL,
    authors JSONB DEFAULT '[]'::jsonb,
    year INTEGER,
    abstract TEXT,
    url TEXT,
    doi VARCHAR(255),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Ensure unique external references
    CONSTRAINT publications_source_external_id_unique UNIQUE (source, external_id)
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_publications_source ON core.publications(source);
CREATE INDEX IF NOT EXISTS idx_publications_year ON core.publications(year);
CREATE INDEX IF NOT EXISTS idx_publications_doi ON core.publications(doi);

-- Full-text search index on title and abstract
CREATE INDEX IF NOT EXISTS idx_publications_fts ON core.publications 
    USING gin(to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(abstract, '')));

-- Junction table for linking publications to taxa
CREATE TABLE IF NOT EXISTS core.publication_taxa (
    publication_id VARCHAR(64) NOT NULL REFERENCES core.publications(id) ON DELETE CASCADE,
    taxon_id UUID NOT NULL REFERENCES core.taxa(id) ON DELETE CASCADE,
    relevance_score FLOAT DEFAULT 1.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    PRIMARY KEY (publication_id, taxon_id)
);

CREATE INDEX IF NOT EXISTS idx_publication_taxa_taxon ON core.publication_taxa(taxon_id);

-- Add comments
COMMENT ON TABLE core.publications IS 'Mycological research publications from external sources';
COMMENT ON COLUMN core.publications.source IS 'Source of publication: gbif, pubmed, semantic_scholar, etc.';
COMMENT ON COLUMN core.publications.external_id IS 'ID from the source system';
COMMENT ON COLUMN core.publications.metadata IS 'Additional source-specific metadata';
COMMENT ON TABLE core.publication_taxa IS 'Links publications to relevant taxa';
