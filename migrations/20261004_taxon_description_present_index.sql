-- list_taxa has_description filter: probe the few taxa with a description instead of trimming every row.
-- Apply on a live database outside a transaction (CONCURRENTLY), e.g.:
--   psql -v ON_ERROR_STOP=1 -f migrations/20261004_taxon_description_present_index.sql
-- Reverse with migrations/20261004_taxon_description_present_index.down.sql.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_taxon_description_present
    ON core.taxon (id)
    WHERE description IS NOT NULL;
