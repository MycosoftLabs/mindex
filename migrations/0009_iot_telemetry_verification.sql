-- IoT telemetry verification + replay + health state
-- Created: FEB09 2026

BEGIN;

ALTER TABLE telemetry.sample
    ADD COLUMN IF NOT EXISTS verified boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS verified_at timestamptz,
    ADD COLUMN IF NOT EXISTS verified_by text,
    ADD COLUMN IF NOT EXISTS verification_method text,
    ADD COLUMN IF NOT EXISTS verification_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS envelope_msg_id text,
    ADD COLUMN IF NOT EXISTS envelope_seq bigint,
    ADD COLUMN IF NOT EXISTS envelope_hash text,
    ADD COLUMN IF NOT EXISTS envelope_sig text,
    ADD COLUMN IF NOT EXISTS dedupe_key text;

CREATE UNIQUE INDEX IF NOT EXISTS ux_sample_dedupe_key
    ON telemetry.sample (dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sample_verified_recorded_at
    ON telemetry.sample (verified, recorded_at DESC)
    WHERE verified = true;

CREATE TABLE IF NOT EXISTS telemetry.replay_state (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES telemetry.device (id) ON DELETE CASCADE,
    stream_id uuid REFERENCES telemetry.stream (id) ON DELETE SET NULL,
    replay_type text NOT NULL,
    start_time timestamptz NOT NULL,
    end_time timestamptz,
    current_position timestamptz NOT NULL,
    playback_speed double precision NOT NULL DEFAULT 1.0,
    is_playing boolean NOT NULL DEFAULT false,
    is_paused boolean NOT NULL DEFAULT false,
    filters jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_replay_state_device_playing
    ON telemetry.replay_state (device_id, is_playing);

CREATE TABLE IF NOT EXISTS telemetry.device_health_state (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id uuid NOT NULL REFERENCES telemetry.device (id) ON DELETE CASCADE,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL,
    health_score double precision,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    alerts jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_health_state_device_time
    ON telemetry.device_health_state (device_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_health_state_status_time
    ON telemetry.device_health_state (status, recorded_at DESC);

COMMIT;
