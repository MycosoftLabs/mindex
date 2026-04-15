-- MYCODAO vs Mycosoft data separation in MINDEX (Apr 2026)
-- - Schema `mycodao`: Pulse, prediction-market-style aggregates, wallets, signals, Telegram, x402 audit (MYCODAO product line)
-- - Schema `mycosoft`: Cross-product registry/metadata so MYCA and operators know which PG schemas belong to which zone
-- Existing Mycosoft domain schemas (core, obs, fusarium, telemetry, ...) are unchanged; MYCA continues to use internal MINDEX APIs.

CREATE SCHEMA IF NOT EXISTS mycodao;
COMMENT ON SCHEMA mycodao IS
    'MYCODAO Pulse / prediction markets / wallet / DAO-adjacent derived data — isolated from Mycosoft core biology/earth pipelines.';

CREATE SCHEMA IF NOT EXISTS mycosoft;
COMMENT ON SCHEMA mycosoft IS
    'Mycosoft platform metadata and data-zone registry; MYCA uses this with mycodao.* and domain schemas.';

-- Discoverability for MYCA (MAS): which logical zones map to which PostgreSQL schemas
CREATE TABLE IF NOT EXISTS mycosoft.data_zone_registry (
    zone_code TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    pg_schema TEXT NOT NULL,
    product_line TEXT NOT NULL CHECK (product_line IN ('mycodao', 'mycosoft', 'shared_infra')),
    notes TEXT
);

COMMENT ON TABLE mycosoft.data_zone_registry IS
    'Logical zones vs physical schemas. MYCA internal token can query all; this table documents boundaries.';

INSERT INTO mycosoft.data_zone_registry (zone_code, display_name, pg_schema, product_line, notes)
VALUES
    (
        'mycodao_pulse',
        'MYCODAO Pulse intelligence',
        'mycodao',
        'mycodao',
        'Polymarket-style snapshots, wallet stats, signals, ingestion runs, telegram, x402 audit — MYCODAO only.'
    ),
    (
        'mycosoft_registry',
        'Mycosoft platform registry',
        'mycosoft',
        'mycosoft',
        'This schema: zone registry and future cross-product Mycosoft-only metadata tables.'
    ),
    (
        'mycosoft_fusarium',
        'FUSARIUM analytics',
        'fusarium',
        'mycosoft',
        'Defense/biosecurity analytics — Mycosoft product data.'
    ),
    (
        'mycosoft_core_bio',
        'MINDEX core / obs / bio',
        'core, obs, bio, species',
        'mycosoft',
        'Taxonomy, observations, compounds — Mycosoft MINDEX core; not MYCODAO financial/Pulse.'
    ),
    (
        'mycosoft_telemetry',
        'Device telemetry',
        'telemetry',
        'mycosoft',
        'MycoBrain and device streams — Mycosoft platform.'
    ),
    (
        'shared_mica_ledger',
        'MICA merkle / experience',
        'mica, public (experience_packets)',
        'shared_infra',
        'Cross-cutting MYCA state; not MYCODAO Pulse tables.'
    )
ON CONFLICT (zone_code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- MYCODAO-only tables (never mix with core/obs rows)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mycodao.polymarket_market_snapshots (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    question TEXT,
    outcomes JSONB,
    volume_usd NUMERIC(24, 6),
    liquidity_usd NUMERIC(24, 6),
    raw_ref JSONB,
    source_version TEXT,
    content_checksum TEXT
);

CREATE INDEX IF NOT EXISTS idx_mycodao_pm_snapshots_market_time
    ON mycodao.polymarket_market_snapshots (market_id, snapshot_at DESC);

CREATE TABLE IF NOT EXISTS mycodao.wallet_stats (
    id BIGSERIAL PRIMARY KEY,
    wallet_pubkey TEXT NOT NULL,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    metrics JSONB NOT NULL DEFAULT '{}'::JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_mycodao_wallet_period UNIQUE (wallet_pubkey, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_mycodao_wallet_stats_pubkey
    ON mycodao.wallet_stats (wallet_pubkey);

CREATE TABLE IF NOT EXISTS mycodao.market_scores (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    score NUMERIC(12, 6),
    factors JSONB,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mycodao_market_scores_market
    ON mycodao.market_scores (market_id, computed_at DESC);

CREATE TABLE IF NOT EXISTS mycodao.signal_events (
    id BIGSERIAL PRIMARY KEY,
    signal_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    severity TEXT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mycodao_signals_time
    ON mycodao.signal_events (observed_at DESC);

CREATE TABLE IF NOT EXISTS mycodao.ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    records_upserted INTEGER DEFAULT 0,
    error_message TEXT,
    source_version TEXT,
    payload_checksum TEXT
);

CREATE INDEX IF NOT EXISTS idx_mycodao_ingestion_started
    ON mycodao.ingestion_runs (started_at DESC);

CREATE TABLE IF NOT EXISTS mycodao.telegram_messages (
    id BIGSERIAL PRIMARY KEY,
    channel_id TEXT NOT NULL,
    message_hash TEXT NOT NULL,
    message_at TIMESTAMPTZ NOT NULL,
    normalized_text TEXT,
    meta JSONB,
    CONSTRAINT uq_mycodao_tg_channel_hash UNIQUE (channel_id, message_hash)
);

CREATE INDEX IF NOT EXISTS idx_mycodao_tg_time
    ON mycodao.telegram_messages (message_at DESC);

CREATE TABLE IF NOT EXISTS mycodao.realms_proposal_mirror (
    id BIGSERIAL PRIMARY KEY,
    realm_pubkey TEXT NOT NULL,
    proposal_pubkey TEXT,
    title TEXT,
    state TEXT,
    content_hash TEXT,
    detail_url TEXT,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    meta JSONB
);

CREATE INDEX IF NOT EXISTS idx_mycodao_realms_realm
    ON mycodao.realms_proposal_mirror (realm_pubkey, observed_at DESC);

CREATE TABLE IF NOT EXISTS mycodao.x402_audit_log (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT,
    policy_id TEXT,
    simulate_mode BOOLEAN NOT NULL DEFAULT TRUE,
    amount_requested NUMERIC(24, 8),
    currency TEXT,
    http_resource TEXT,
    status TEXT NOT NULL,
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mycodao_x402_time
    ON mycodao.x402_audit_log (created_at DESC);
