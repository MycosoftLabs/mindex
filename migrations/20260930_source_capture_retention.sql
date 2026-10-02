-- Explicit setup only. No route or worker applies this migration.
BEGIN;
CREATE SCHEMA IF NOT EXISTS raw_source;
CREATE TABLE IF NOT EXISTS raw_source.capture (
    capture_id UUID PRIMARY KEY,
    service TEXT NOT NULL,
    source_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    metadata_sha256 TEXT NOT NULL CHECK (length(metadata_sha256) = 64),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_length BIGINT NOT NULL CHECK (byte_length > 0),
    media_type TEXT NOT NULL,
    content_encoding TEXT NOT NULL,
    observed_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    classification TEXT NOT NULL DEFAULT 'public_source' CHECK (classification = 'public_source'),
    state TEXT NOT NULL DEFAULT 'pending_archive'
        CHECK (state IN ('pending_archive', 'archiving', 'archived_verified', 'integrity_blocked')),
    payload BYTEA,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    last_error_code TEXT,
    object_bucket TEXT,
    object_key TEXT,
    object_version TEXT,
    verified_at TIMESTAMPTZ,
    UNIQUE (service, source_id, idempotency_key),
    CHECK ((payload IS NULL AND state = 'archived_verified') OR
           (payload IS NOT NULL AND octet_length(payload) = byte_length)),
    CHECK (state <> 'archived_verified' OR
           (object_bucket IS NOT NULL AND object_key IS NOT NULL AND
            object_version IS NOT NULL AND verified_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS source_capture_due_idx
    ON raw_source.capture (next_attempt_at, captured_at)
    WHERE state IN ('pending_archive', 'archiving');
COMMIT;
