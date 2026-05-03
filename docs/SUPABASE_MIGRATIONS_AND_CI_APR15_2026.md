# Supabase migrations & compliance automation — April 15, 2026

**Status:** Operational  
**Location:** `supabase/` and `scripts/supabase_compliance_checks.sql` in this repo (MINDEX).

## Why MINDEX holds Supabase SQL

Hosted Supabase Postgres powers mycosoft.com auth and shared tables (`profiles`, `mindex_data`, etc.). Versioned SQL lives next to `docs/mycosoft_cmmc_nist_compliance_report.md` so compliance and schema changes stay in one place. The Next.js app in `WEBSITE/website` consumes the database via env; it does not need a duplicate `supabase/migrations` tree unless you prefer to symlink.

## Apply migrations

1. Install [Supabase CLI](https://supabase.com/docs/guides/cli).
2. `cd` to this repo root (where `supabase/config.toml` is).
3. `supabase login` (personal access token).
4. `supabase link --project-ref hnevnsxnhfibhbsipqvz`
5. `supabase db push` — applies new files under `supabase/migrations/`.

Do not commit database passwords. Use Supabase dashboard or CI secrets only.

## Compliance checks

Read-only SQL: `scripts/supabase_compliance_checks.sql` (anon posture: no non-SELECT table grants, exactly three anon SELECT tables, no anon EXECUTE on routines).

```bash
psql "$SUPABASE_COMPLIANCE_DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/supabase_compliance_checks.sql
```

Set `SUPABASE_COMPLIANCE_DATABASE_URL` in GitHub Actions (repository secret) to run the same checks in CI (see `.github/workflows/supabase-compliance.yml`).

## Migrations added Apr 15, 2026

| File | Purpose |
|------|---------|
| `20260415180000_admin_registry_trigger_functions.sql` | `handle_new_user` / `handle_super_admin_role` use `admin_registry` (no hardcoded admin email). |
| `20260415180100_mindex_data_invalid_merkle_cleanup.sql` | Delete invalid `merkle_root` rows in `public.mindex_data`. |
| `20260415190200_rls_writes_and_storage_listing_hardening.sql` | Drop permissive authenticated INSERT/UPDATE RLS on `audit_logs`, `defense_briefing_requests`, `incidents`, `security_events`; drop broad `storage.objects` SELECT for `avatars` / `species-images` (listing). |

## Manual items (unchanged)

- Leaked password protection: Supabase Dashboard → Auth.
- Edge function audits: `process-telemetry`, `generate-embeddings` (CORS, secrets, errors).
- Optional: private `avatars` / `species-images` buckets.
