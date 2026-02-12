-- 0010_api_keys.sql
-- Date: FEB09 2026
--
-- Purpose:
-- - Provide a shared API key store used by Mycorrhizae Protocol (and optionally other services)
-- - Supports: key generation, validation, rotation, audit logging, and rate limiting
--
-- Notes:
-- - Keys are stored as SHA-256 hashes (raw keys are never stored).
-- - This is intentionally "public schema" because Mycorrhizae connects to MINDEX Postgres directly.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key_hash text UNIQUE NOT NULL,
  key_prefix text NOT NULL,
  name text NOT NULL,
  description text,
  owner_id uuid,
  service text NOT NULL,
  scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
  rate_limit_per_minute integer NOT NULL DEFAULT 60,
  rate_limit_per_day integer NOT NULL DEFAULT 10000,
  expires_at timestamptz,
  last_used_at timestamptz,
  usage_count integer NOT NULL DEFAULT 0,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  rotated_from uuid REFERENCES api_keys(id),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_api_keys_service ON api_keys(service);
CREATE INDEX IF NOT EXISTS ix_api_keys_is_active ON api_keys(is_active);

CREATE TABLE IF NOT EXISTS api_key_usage (
  key_id uuid NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
  window_start timestamptz NOT NULL,
  window_type text NOT NULL,
  request_count integer NOT NULL DEFAULT 0,
  PRIMARY KEY (key_id, window_start, window_type)
);

CREATE TABLE IF NOT EXISTS api_key_audit (
  id bigserial PRIMARY KEY,
  key_id uuid NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
  action text NOT NULL,
  ip_address inet,
  user_agent text,
  endpoint text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

