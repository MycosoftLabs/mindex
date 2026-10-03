-- All-species ancestry database: indexes for prefix browse and name search over millions of taxa.
-- Apply on a live database outside a transaction (CONCURRENTLY), e.g.:
--   psql -v ON_ERROR_STOP=1 -f migrations/20261003_taxon_all_species_indexes.sql
-- Indexes are not part of logical replication; each target builds its own.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- list_taxa prefix filter: lower(canonical_name) LIKE 'abc%'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_taxon_canon_lower_pattern
    ON core.taxon (lower(canonical_name) text_pattern_ops);

-- list_taxa default ordering within a rank (rank = ANY(...) ORDER BY canonical_name)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_taxon_rank_canonical
    ON core.taxon (rank, canonical_name);

-- bulk_taxonomy_ingest: "taxon already linked to this source" lookups and the /taxa/stats join
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_taxon_external_taxon_source
    ON core.taxon_external_id (taxon_id, source);

-- list_taxa free-text search: canonical_name / common_name ILIKE '%q%'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_taxon_canonical_trgm
    ON core.taxon USING gin (canonical_name gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_taxon_common_trgm
    ON core.taxon USING gin (common_name gin_trgm_ops);
