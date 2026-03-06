-- Beta Users Table - March 5, 2026
-- Tracks beta signups for revenue validation (pricing page, onboarding flow)
-- Referenced by MYCA Loop Closure Plan

CREATE TABLE IF NOT EXISTS beta_users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  plan TEXT NOT NULL DEFAULT 'free',
  signup_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  api_key_hash TEXT,
  api_key_prefix TEXT,
  usage_count INTEGER NOT NULL DEFAULT 0,
  supabase_user_id UUID,
  stripe_customer_id TEXT,
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_beta_users_email ON beta_users(email);
CREATE INDEX IF NOT EXISTS ix_beta_users_plan ON beta_users(plan);
CREATE INDEX IF NOT EXISTS ix_beta_users_signup_date ON beta_users(signup_date DESC);
