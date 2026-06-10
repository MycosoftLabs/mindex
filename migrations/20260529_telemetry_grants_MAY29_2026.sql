-- Grant MINDEX API role access to telemetry schema (envelope ingest).
BEGIN;

GRANT USAGE ON SCHEMA telemetry TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA telemetry TO mindex;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA telemetry TO mindex;

ALTER DEFAULT PRIVILEGES IN SCHEMA telemetry
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mindex;

-- Align with envelope ingest ON CONFLICT (dedupe_key)
DROP INDEX IF EXISTS ux_sample_dedupe_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_sample_dedupe_key ON telemetry.sample (dedupe_key);

COMMIT;
