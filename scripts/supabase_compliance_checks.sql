-- Run against Supabase Postgres (session role must see information_schema / pg_catalog).
-- Usage: psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f scripts/supabase_compliance_checks.sql
-- Fails with ERROR if CMMC anon posture regresses.

\set ON_ERROR_STOP on

-- 1) anon must not have INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER on public tables
DO $$
DECLARE
  bad integer;
BEGIN
  SELECT count(*)::integer INTO bad
  FROM information_schema.table_privileges
  WHERE grantee = 'anon'
    AND table_schema = 'public'
    AND privilege_type <> 'SELECT';

  IF bad > 0 THEN
    RAISE EXCEPTION 'compliance: anon has % non-SELECT table privileges (expected 0)', bad;
  END IF;
END $$;

-- 2) anon SELECT only on the three intentional public catalog tables
DO $$
DECLARE
  c integer;
BEGIN
  SELECT count(DISTINCT table_name)::integer INTO c
  FROM information_schema.table_privileges
  WHERE grantee = 'anon'
    AND table_schema = 'public'
    AND privilege_type = 'SELECT';

  IF c <> 3 THEN
    RAISE EXCEPTION 'compliance: expected 3 distinct tables with anon SELECT, got %', c;
  END IF;
END $$;

-- 3) anon must not EXECUTE any public schema routines
DO $$
DECLARE
  n integer;
BEGIN
  SELECT count(*)::integer INTO n
  FROM information_schema.routine_privileges
  WHERE grantee = 'anon'
    AND routine_schema = 'public'
    AND privilege_type = 'EXECUTE';

  IF n > 0 THEN
    RAISE EXCEPTION 'compliance: anon has % EXECUTE grants on public routines (expected 0)', n;
  END IF;
END $$;

SELECT 'supabase_compliance_checks: OK' AS status;
