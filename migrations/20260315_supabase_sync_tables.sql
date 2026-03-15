-- ============================================================================
-- Supabase Sync Support Tables
-- March 15, 2026
--
-- Local tables that track what has been synced to Supabase, manage the
-- on-demand scrape pipeline, and support the tiered storage strategy.
-- ============================================================================

-- ============================================================================
-- SCRAPE TRACKING — Know what we've already scraped and stored locally
-- ============================================================================

CREATE TABLE IF NOT EXISTS app.scrape_log (
    id              SERIAL PRIMARY KEY,
    domain          VARCHAR(50) NOT NULL,
    query           TEXT NOT NULL,
    source          VARCHAR(100) NOT NULL,
    records_count   INTEGER DEFAULT 0,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stored_locally  BOOLEAN DEFAULT TRUE,
    synced_supabase BOOLEAN DEFAULT FALSE,
    synced_nas      BOOLEAN DEFAULT FALSE,
    ttl_expires     TIMESTAMPTZ,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_scrape_domain_query ON app.scrape_log (domain, query);
CREATE INDEX IF NOT EXISTS idx_scrape_time ON app.scrape_log (scraped_at DESC);

-- ============================================================================
-- SYNC LEDGER — Track what's been synced to Supabase
-- ============================================================================

CREATE TABLE IF NOT EXISTS app.supabase_sync_ledger (
    id              SERIAL PRIMARY KEY,
    table_name      VARCHAR(100) NOT NULL,
    last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_synced_id  TEXT,
    records_synced  INTEGER DEFAULT 0,
    sync_direction  VARCHAR(10) DEFAULT 'push',  -- push (local→supa) or pull (supa→local)
    status          VARCHAR(20) DEFAULT 'ok',
    error_message   TEXT,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_sync_table ON app.supabase_sync_ledger (table_name);

-- ============================================================================
-- STORAGE TIER TRACKING — Where is each piece of data stored?
-- ============================================================================

CREATE TABLE IF NOT EXISTS app.storage_manifest (
    id              SERIAL PRIMARY KEY,
    entity_type     VARCHAR(100) NOT NULL,
    entity_id       TEXT NOT NULL,
    tier_local      BOOLEAN DEFAULT TRUE,    -- PostgreSQL (hot)
    tier_supabase   BOOLEAN DEFAULT FALSE,   -- Supabase (warm)
    tier_nas        BOOLEAN DEFAULT FALSE,   -- NAS (cold)
    nas_path        TEXT,                    -- File path on NAS
    supabase_table  VARCHAR(100),           -- Supabase table name
    size_bytes      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at     TIMESTAMPTZ,
    UNIQUE (entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_manifest_type ON app.storage_manifest (entity_type);
CREATE INDEX IF NOT EXISTS idx_manifest_tier ON app.storage_manifest (tier_local, tier_supabase, tier_nas);

-- ============================================================================
-- CACHE STATS — Track cache hit rates for optimization
-- ============================================================================

CREATE TABLE IF NOT EXISTS app.cache_stats (
    id              SERIAL PRIMARY KEY,
    hour            TIMESTAMPTZ NOT NULL,
    domain          VARCHAR(50) NOT NULL,
    cache_hits      INTEGER DEFAULT 0,
    cache_misses    INTEGER DEFAULT 0,
    local_hits      INTEGER DEFAULT 0,
    supabase_hits   INTEGER DEFAULT 0,
    live_scrapes    INTEGER DEFAULT 0,
    avg_latency_ms  DOUBLE PRECISION,
    UNIQUE (hour, domain)
);
CREATE INDEX IF NOT EXISTS idx_cache_stats_time ON app.cache_stats (hour DESC);
