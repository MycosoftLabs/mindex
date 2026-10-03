-- Additive, manual-only migration. Requires the brief09 retention.v1 deployment.
BEGIN;
CREATE SCHEMA IF NOT EXISTS formspace;
CREATE TABLE IF NOT EXISTS formspace.chart_revision (
    issuer text NOT NULL, subject text NOT NULL, tenant_id uuid NOT NULL,
    project_id uuid NOT NULL, chart_id text NOT NULL, revision bigint NOT NULL,
    chart_hash char(64) NOT NULL, definition jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (issuer, subject, tenant_id, project_id, chart_id, revision)
);
CREATE TABLE IF NOT EXISTS formspace.job (
    job_id uuid PRIMARY KEY, issuer text NOT NULL, subject text NOT NULL,
    tenant_id uuid NOT NULL, project_id uuid NOT NULL, idempotency_key text NOT NULL,
    request_hash char(64) NOT NULL, chart_hash char(64) NOT NULL, dataset_hash char(64) NOT NULL,
    request jsonb NOT NULL, state text NOT NULL DEFAULT 'admitted'
        CHECK (state IN ('admitted','running','archiving','completed','cancelled','failed')),
    output_bytes bytea, output_sha256 char(64), artifact_id uuid,
    artifact_state text NOT NULL DEFAULT 'pending', memory_state text NOT NULL DEFAULT 'pending',
    memory_id uuid,
    replica_state text NOT NULL DEFAULT 'unavailable', error_code text,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject, tenant_id, project_id, idempotency_key),
    CHECK (octet_length(request::text) <= 1048576),
    CHECK (output_bytes IS NULL OR octet_length(output_bytes) <= 2097152),
    CHECK (state <> 'completed' OR (artifact_id IS NOT NULL AND artifact_state = 'verified'))
);
CREATE INDEX IF NOT EXISTS formspace_job_owner ON formspace.job
    (issuer, subject, tenant_id, project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS formspace.outbox (
    job_id uuid PRIMARY KEY REFERENCES formspace.job(job_id),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token text, lease_until timestamptz, worker_id text,
    fence bigint NOT NULL DEFAULT 0, attempts integer NOT NULL DEFAULT 0,
    done boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS formspace_outbox_ready ON formspace.outbox(available_at)
    WHERE NOT done;
CREATE OR REPLACE FUNCTION formspace.reject_revision_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'FormSpace chart revisions are immutable';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS immutable_chart_revision ON formspace.chart_revision;
CREATE TRIGGER immutable_chart_revision BEFORE UPDATE ON formspace.chart_revision
FOR EACH ROW EXECUTE FUNCTION formspace.reject_revision_change();
CREATE OR REPLACE FUNCTION formspace.reject_job_input_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.issuer,NEW.subject,NEW.tenant_id,NEW.project_id,NEW.request,NEW.request_hash,
        NEW.chart_hash,NEW.dataset_hash,NEW.idempotency_key,NEW.created_at)
        IS DISTINCT FROM
       (OLD.issuer,OLD.subject,OLD.tenant_id,OLD.project_id,OLD.request,OLD.request_hash,
        OLD.chart_hash,OLD.dataset_hash,OLD.idempotency_key,OLD.created_at) THEN
        RAISE EXCEPTION 'FormSpace job identity and input are immutable';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS immutable_job_input ON formspace.job;
CREATE TRIGGER immutable_job_input BEFORE UPDATE ON formspace.job
FOR EACH ROW EXECUTE FUNCTION formspace.reject_job_input_change();
-- Only the reviewed MINDEX service DB role may access this schema. No anonymous,
-- Supabase public, or browser role is granted table access by this migration.
REVOKE ALL ON SCHEMA formspace FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA formspace FROM PUBLIC;
COMMIT;
