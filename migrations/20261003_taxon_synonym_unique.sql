-- All-species ingest: idempotent synonym loads (one row per accepted taxon and synonym name).
-- Additive and index-only, so logical replication is unaffected; each target builds its own index.
-- Apply on a live database outside a transaction:
--   psql -v ON_ERROR_STOP=1 -f migrations/20261003_taxon_synonym_unique.sql

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_taxon_synonym_taxon_lower
    ON core.taxon_synonym (taxon_id, lower(synonym));

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_taxon_synonym_lower
    ON core.taxon_synonym (lower(synonym));
