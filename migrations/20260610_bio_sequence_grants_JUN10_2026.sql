-- Grant sequence usage for tables created after 20260603_grants_bio_obs_core.sql (e.g. 0012 genetics).
BEGIN;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA bio TO mindex;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO mindex;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA obs TO mindex;

COMMIT;
