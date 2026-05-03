-- CMMC / Security Advisor follow-up (Apr 15, 2026)
-- 1) Remove permissive authenticated INSERT/UPDATE RLS on security tables.
--    Server routes use SUPABASE_SERVICE_ROLE_KEY and bypass RLS; direct client writes must not.
-- 2) Drop broad storage.objects SELECT policies for avatars & species-images (listing/metadata).
--    Verify public image URLs after deploy; use signed URLs or a proxy if anything regresses.
--
-- Ref: docs/mycosoft_cmmc_nist_compliance_report.md

-- ---------------------------------------------------------------------------
-- A) Security tables: drop INSERT/UPDATE policies that apply to authenticated
--    (or PUBLIC / all roles via empty roles array).
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT schemaname, tablename, policyname, cmd
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = ANY (
        ARRAY[
          'audit_logs',
          'defense_briefing_requests',
          'incidents',
          'security_events'
        ]
      )
      AND cmd IN ('INSERT', 'UPDATE')
      AND (
        -- Supabase: NULL or empty roles = policy applies broadly
        roles IS NULL
        OR coalesce(cardinality(roles), 0) = 0
        OR 'authenticated'::name = ANY (roles)
      )
  LOOP
    EXECUTE format(
      'DROP POLICY IF EXISTS %I ON %I.%I',
      r.policyname,
      r.schemaname,
      r.tablename
    );
    RAISE NOTICE 'Dropped % policy % on %.%',
      r.cmd,
      r.policyname,
      r.schemaname,
      r.tablename;
  END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- B) storage.objects: drop SELECT policies that expose avatars / species-images
--    bucket contents (listing). Tuned to common bucket_id = '...' patterns.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT policyname, qual::text AS q
    FROM pg_policies
    WHERE schemaname = 'storage'
      AND tablename = 'objects'
      AND cmd = 'SELECT'
      AND (
        qual::text ~* 'bucket_id\s*=\s*''avatars'''
        OR qual::text ~* 'bucket_id\s*=\s*''species-images'''
        OR (
          trim(both from qual::text) IN ('true', '(true)')
          AND (
            policyname ILIKE '%avatar%'
            OR policyname ILIKE '%species%'
          )
        )
      )
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON storage.objects', r.policyname);
    RAISE NOTICE 'Dropped storage.objects SELECT policy % (qual: %)', r.policyname, r.q;
  END LOOP;
END $$;
