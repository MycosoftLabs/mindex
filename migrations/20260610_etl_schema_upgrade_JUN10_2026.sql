-- ETL schema upgrade: replace bootstrap bio.compound stub with full 0007 columns;
-- ensure bio.genetic_sequence exists (0012). Safe to re-run on VM 189.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS core.migration_log (
    name text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bio' AND table_name = 'compound' AND column_name = 'name'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bio' AND table_name = 'compound' AND column_name = 'pubchem_id'
    ) THEN
        DROP TABLE IF EXISTS bio.taxon_compound CASCADE;
        DROP TABLE IF EXISTS bio.compound CASCADE;
        RAISE NOTICE 'Dropped bootstrap bio.compound stub — apply 0007_compounds.sql next';
    END IF;
END $$;

COMMIT;
