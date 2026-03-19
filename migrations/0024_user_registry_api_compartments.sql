-- 0024_user_registry_api_compartments.sql
-- Date: MAR19 2026
--
-- Purpose:
-- - Create user_registry table unifying identity for humans, agents, and services
-- - Bridge beta_users to api_keys via user_registry
-- - Add missing index on api_keys(key_hash) for fast lookup
-- - Add user_id FK on api_keys pointing to user_registry
-- - Backfill existing beta_users into user_registry and api_keys

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =========================================================================
-- USER REGISTRY — unified identity for all API consumers
-- =========================================================================

CREATE TABLE IF NOT EXISTS user_registry (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT UNIQUE,
    display_name    TEXT,
    user_type       TEXT NOT NULL DEFAULT 'human'
                    CHECK (user_type IN ('human', 'agent', 'service')),
    plan_tier       TEXT NOT NULL DEFAULT 'free'
                    CHECK (plan_tier IN ('free', 'pro', 'enterprise', 'internal')),
    stripe_customer_id TEXT,
    payment_status  TEXT NOT NULL DEFAULT 'pending'
                    CHECK (payment_status IN ('pending', 'active', 'past_due', 'cancelled')),
    startup_fee_paid BOOLEAN NOT NULL DEFAULT FALSE,
    agent_metadata  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_user_registry_email ON user_registry(email);
CREATE INDEX IF NOT EXISTS ix_user_registry_user_type ON user_registry(user_type);
CREATE INDEX IF NOT EXISTS ix_user_registry_plan_tier ON user_registry(plan_tier);

-- =========================================================================
-- EXTEND api_keys — link to user_registry
-- =========================================================================

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES user_registry(id);
CREATE INDEX IF NOT EXISTS ix_api_keys_user_id ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS ix_api_keys_key_hash ON api_keys(key_hash);

-- =========================================================================
-- BACKFILL — migrate existing beta_users into user_registry + api_keys
-- =========================================================================

-- Step 1: Insert beta_users into user_registry (skip dupes)
INSERT INTO user_registry (email, user_type, plan_tier, stripe_customer_id, payment_status, startup_fee_paid, created_at, updated_at)
SELECT
    bu.email,
    'human',
    COALESCE(bu.plan, 'free'),
    bu.stripe_customer_id,
    CASE WHEN bu.stripe_customer_id IS NOT NULL THEN 'active' ELSE 'pending' END,
    bu.stripe_customer_id IS NOT NULL,
    COALESCE(bu.created_at::timestamptz, now()),
    now()
FROM beta_users bu
WHERE bu.email IS NOT NULL
ON CONFLICT (email) DO NOTHING;

-- Step 2: Insert beta_users api keys into api_keys table (skip dupes)
INSERT INTO api_keys (key_hash, key_prefix, name, service, scopes, rate_limit_per_minute, rate_limit_per_day, user_id, is_active, created_at, updated_at)
SELECT
    bu.api_key_hash,
    COALESCE(bu.api_key_prefix, LEFT(bu.api_key_hash, 12)),
    bu.email || ' (migrated from beta)',
    'worldview',
    '["worldview:read"]'::jsonb,
    CASE bu.plan
        WHEN 'enterprise' THEN 300
        WHEN 'pro' THEN 60
        ELSE 10
    END,
    CASE bu.plan
        WHEN 'enterprise' THEN 100000
        WHEN 'pro' THEN 10000
        ELSE 1000
    END,
    ur.id,
    TRUE,
    COALESCE(bu.created_at::timestamptz, now()),
    now()
FROM beta_users bu
JOIN user_registry ur ON ur.email = bu.email
WHERE bu.api_key_hash IS NOT NULL
ON CONFLICT (key_hash) DO UPDATE SET
    user_id = EXCLUDED.user_id,
    service = 'worldview',
    updated_at = now();
