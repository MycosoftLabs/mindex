-- ============================================================================
-- MINDEX Agent Runtime — June 9, 2026
--
-- Persistent state for the MINDEX orchestrator and its per-source sub-agents.
-- This is what makes the ETL "always on": agent schedules, run history,
-- orchestrator heartbeat, and AWS/NAS backup history all survive restarts
-- so the runtime can resume exactly where it left off.
--
-- Idempotent: safe to run repeatedly. The orchestrator also calls an
-- equivalent ensure_schema() at startup so it works even on DBs where this
-- migration was never applied.
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS etl;

-- ----------------------------------------------------------------------------
-- source_agent — registry + live state for every sub-agent the orchestrator
-- supervises. One row per source (gbif, inat_obs, genbank, ...) plus the
-- system maintenance agents (supabase_sync, aws_backup_pg, s3_inventory, ...).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl.source_agent (
    name                  TEXT PRIMARY KEY,
    source                TEXT NOT NULL DEFAULT '',
    kind                  TEXT NOT NULL DEFAULT 'source',   -- 'source' | 'system'
    concurrency_group     TEXT NOT NULL DEFAULT 'default',
    description           TEXT NOT NULL DEFAULT '',
    enabled               BOOLEAN NOT NULL DEFAULT TRUE,
    priority              INTEGER NOT NULL DEFAULT 100,
    schedule_seconds      INTEGER NOT NULL DEFAULT 86400,
    max_pages             INTEGER,
    domain_mode           TEXT,
    -- live state -------------------------------------------------------------
    status                TEXT NOT NULL DEFAULT 'idle',      -- idle|running|cooldown|disabled|failed
    last_run_at           TIMESTAMPTZ,
    last_finished_at      TIMESTAMPTZ,
    next_run_at           TIMESTAMPTZ,
    cooldown_until        TIMESTAMPTZ,
    last_status           TEXT,                              -- success|rate_limited|downtime|error
    last_records          INTEGER NOT NULL DEFAULT 0,
    last_duration_ms      INTEGER NOT NULL DEFAULT 0,
    last_error            TEXT,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    total_runs            BIGINT NOT NULL DEFAULT 0,
    total_records         BIGINT NOT NULL DEFAULT 0,
    watermark             JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_source_agent_due
    ON etl.source_agent (enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_source_agent_status
    ON etl.source_agent (status);

-- ----------------------------------------------------------------------------
-- agent_run — append-only history of every agent invocation. Powers the
-- livestream, run analytics, and "is the ETL actually working?" checks.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl.agent_run (
    id            BIGSERIAL PRIMARY KEY,
    agent_name    TEXT NOT NULL,
    source        TEXT,
    status        TEXT NOT NULL DEFAULT 'running',  -- running|success|rate_limited|downtime|error
    records       INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER,
    error         TEXT,
    host          TEXT,
    pid           INTEGER,
    cycle         BIGINT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_agent_run_agent_time
    ON etl.agent_run (agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_run_time
    ON etl.agent_run (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_run_status
    ON etl.agent_run (status, started_at DESC);

-- ----------------------------------------------------------------------------
-- orchestrator_heartbeat — single row per orchestrator process. The API uses
-- last_beat_at to report whether the runtime is alive.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl.orchestrator_heartbeat (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    host            TEXT,
    pid             INTEGER,
    started_at      TIMESTAMPTZ,
    last_beat_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cycle           BIGINT NOT NULL DEFAULT 0,
    agents_total    INTEGER NOT NULL DEFAULT 0,
    agents_enabled  INTEGER NOT NULL DEFAULT 0,
    agents_running  INTEGER NOT NULL DEFAULT 0,
    max_concurrency INTEGER NOT NULL DEFAULT 0,
    stats           JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT orchestrator_heartbeat_singleton CHECK (id = 1)
);

-- ----------------------------------------------------------------------------
-- backup_log — AWS/NAS backup + snapshot history (pg_dump→S3, NAS manifest,
-- Glacier offload, S3 inventory). Feeds the /agents/backups endpoint.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl.backup_log (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,                    -- pg_dump|nas_manifest|nas_offload|snapshot|s3_inventory
    target        TEXT,                             -- s3://bucket/key or path
    status        TEXT NOT NULL DEFAULT 'running',  -- running|success|skipped|error
    size_bytes    BIGINT,
    object_count  BIGINT,
    storage_class TEXT,
    error         TEXT,
    host          TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_backup_log_kind_time
    ON etl.backup_log (kind, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_backup_log_time
    ON etl.backup_log (started_at DESC);

-- ----------------------------------------------------------------------------
-- Grants — match the project convention (network/bio migrations).
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mindex') THEN
        GRANT USAGE ON SCHEMA etl TO mindex;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA etl TO mindex;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA etl TO mindex;
        ALTER DEFAULT PRIVILEGES IN SCHEMA etl
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mindex;
        ALTER DEFAULT PRIVILEGES IN SCHEMA etl
            GRANT USAGE, SELECT ON SEQUENCES TO mindex;
    END IF;
END$$;

COMMIT;
