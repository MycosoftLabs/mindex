-- Publications table for PubMed / GBIF literature / Semantic Scholar ETL
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
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT publications_source_external_id_unique UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_publications_source ON core.publications (source);
CREATE INDEX IF NOT EXISTS idx_publications_year ON core.publications (year);
CREATE INDEX IF NOT EXISTS idx_publications_doi ON core.publications (doi);

GRANT SELECT, INSERT, UPDATE, DELETE ON core.publications TO mindex;
