-- Reverses 20261004_taxon_description_present_index.sql. Run outside a transaction.
DROP INDEX CONCURRENTLY IF EXISTS core.idx_taxon_description_present;
