-- MINDEX private retention v1. Additive; never execute against production as a test.
-- Membership and grants are operator-provisioned. Application clients cannot self-grant.
BEGIN;
CREATE SCHEMA IF NOT EXISTS retention;

CREATE TABLE IF NOT EXISTS retention.membership (
    issuer text NOT NULL,
    subject text NOT NULL,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    active boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (issuer, subject, tenant_id, project_id)
);

CREATE TABLE IF NOT EXISTS retention.artifact (
    artifact_id text PRIMARY KEY,
    owner_issuer text NOT NULL,
    owner_subject text NOT NULL,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    classification text NOT NULL DEFAULT 'private' CHECK (classification = 'private'),
    kind text NOT NULL CHECK (kind IN ('dataset','chart','artifact')),
    media_type text NOT NULL,
    idempotency_key text NOT NULL,
    fingerprint text NOT NULL CHECK (length(fingerprint) = 64),
    metadata_sha256 text NOT NULL CHECK (length(metadata_sha256) = 64),
    sha256 text NOT NULL CHECK (length(sha256) = 64),
    byte_length bigint NOT NULL CHECK (byte_length >= 0),
    payload bytea,
    source_event_at timestamptz,
    received_at timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz,
    retention_until timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN
        ('pending','archiving','verified','quarantined','cancelled','deleted')),
    object_bucket text,
    object_key text,
    object_version text,
    deletion_requested_at timestamptz,
    physical_deleted_at timestamptz,
    CHECK (payload IS NULL OR octet_length(payload) = byte_length),
    CHECK ((state = 'verified') = (available_at IS NOT NULL)),
    CHECK (state <> 'verified' OR (object_bucket IS NOT NULL AND object_key IS NOT NULL
        AND object_version IS NOT NULL)),
    UNIQUE (owner_issuer, owner_subject, tenant_id, project_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS retention_artifact_scope
    ON retention.artifact (tenant_id, project_id, owner_issuer, owner_subject, received_at DESC);

CREATE TABLE IF NOT EXISTS retention.job (
    job_id text PRIMARY KEY,
    artifact_id text NOT NULL UNIQUE REFERENCES retention.artifact(artifact_id),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN
        ('pending','archiving','verified','quarantined','cancelled','deleted')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_token text,
    lease_expires_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error_code text,
    completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS retention.outbox (
    outbox_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artifact_id text NOT NULL REFERENCES retention.artifact(artifact_id),
    job_id text NOT NULL REFERENCES retention.job(job_id),
    event text NOT NULL CHECK (event IN ('archive','purge_object')),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN
        ('pending','leased','done','cancelled','quarantined')),
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    lease_token text,
    lease_expires_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0,
    UNIQUE (artifact_id, event)
);
CREATE INDEX IF NOT EXISTS retention_outbox_pending ON retention.outbox (event, state, outbox_id);

-- Uploaded versions from a worker whose finalize fence was rejected. These are
-- cleanup evidence, never canonical available artifacts. No source bytes here.
CREATE TABLE IF NOT EXISTS retention.orphan_archive (
    orphan_id text PRIMARY KEY,
    artifact_id text NOT NULL REFERENCES retention.artifact(artifact_id),
    archive_lease_token text NOT NULL,
    object_bucket text NOT NULL,
    object_key text NOT NULL,
    object_version text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','leased','done')),
    lease_token text,
    lease_expires_at timestamptz,
    physical_deleted_at timestamptz,
    UNIQUE (artifact_id,object_bucket,object_key,object_version)
);

CREATE TABLE IF NOT EXISTS retention.access_grant (
    artifact_id text NOT NULL REFERENCES retention.artifact(artifact_id),
    grantee_issuer text NOT NULL,
    grantee_subject text NOT NULL,
    active boolean NOT NULL DEFAULT false,
    PRIMARY KEY (artifact_id, grantee_issuer, grantee_subject)
);

CREATE TABLE IF NOT EXISTS retention.memory_reference (
    memory_id text PRIMARY KEY,
    artifact_id text NOT NULL REFERENCES retention.artifact(artifact_id),
    owner_issuer text NOT NULL,
    owner_subject text NOT NULL,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    summary text NOT NULL CHECK (length(summary) <= 2000),
    proof_sha256 text NOT NULL CHECK (length(proof_sha256) = 64),
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    UNIQUE (artifact_id, owner_issuer, owner_subject)
);

-- A MINDEX service role needs SELECT on membership/access_grant, and the narrow
-- UPDATE(updated_at) privilege on membership for SELECT FOR SHARE. It must have
-- no INSERT/DELETE or UPDATE on active/identity/scope. Operator identity is separate.
-- Role names are site-specific; see docs/retention/POSTGRES_QUALIFICATION.md.
REVOKE ALL ON SCHEMA retention FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA retention FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA retention FROM PUBLIC;
COMMIT;

-- Image rollback: disable retention admission/worker, retain all tables/data.
-- Destructive reversal ONLY for a disposable rehearsal database after backup:
-- DROP SCHEMA retention CASCADE;
