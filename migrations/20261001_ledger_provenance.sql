-- Brief 10: additive private provenance lifecycle. PostgreSQL, MINDEX ledger schema.
-- No existing anchors are promoted, no transactions broadcast, no payload bytes copied.
-- Deployment remains manual. Apply once through the reviewed migration runner.
-- Rollback: revert application routes first; retain these tables and audit evidence.
-- Destructive DROP is deliberately omitted and requires a reviewed retention decision.
BEGIN;
CREATE SCHEMA IF NOT EXISTS ledger;

CREATE TABLE IF NOT EXISTS ledger.provenance_record (
    id varchar(36) PRIMARY KEY,
    issuer varchar(512) NOT NULL,
    subject varchar(256) NOT NULL,
    tenant_id varchar(128) NOT NULL,
    project_id varchar(128) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_hash varchar(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    content_hash varchar(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    evidence json NOT NULL,
    source json NOT NULL,
    state varchar(24) NOT NULL CHECK (state IN
      ('registered','validated','approved','submitted','confirmed','finalized','rejected','failed','reorg')),
    qualification varchar(24) NOT NULL CHECK (qualification IN ('local_only','offline_fixture')),
    version integer NOT NULL CHECK (version > 0),
    approval json,
    chain varchar(32) CHECK (chain IN ('bitcoin_op_return','bitcoin_ordinals','solana','hypergraph')),
    transaction_id varchar(256),
    verification json,
    created_at varchar(32) NOT NULL,
    updated_at varchar(32) NOT NULL,
    CONSTRAINT uq_provenance_registration UNIQUE
      (issuer, subject, tenant_id, project_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS ledger.provenance_event (
    id varchar(36) PRIMARY KEY,
    record_id varchar(36) NOT NULL REFERENCES ledger.provenance_record(id) ON DELETE RESTRICT,
    version integer NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_hash varchar(64) NOT NULL,
    kind varchar(40) NOT NULL,
    from_state varchar(24) NOT NULL,
    to_state varchar(24) NOT NULL,
    actor json NOT NULL,
    detail json NOT NULL,
    created_at varchar(32) NOT NULL,
    CONSTRAINT uq_provenance_event_replay UNIQUE (record_id, idempotency_key),
    CONSTRAINT uq_provenance_event_version UNIQUE (record_id, version)
);
CREATE TABLE IF NOT EXISTS ledger.provenance_queue (
    record_id varchar(36) PRIMARY KEY REFERENCES ledger.provenance_record(id) ON DELETE RESTRICT,
    status varchar(32) NOT NULL,
    attempts integer NOT NULL CHECK (attempts >= 0),
    last_error text,
    updated_at varchar(32) NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger.provenance_receipt (
    id varchar(36) PRIMARY KEY,
    record_id varchar(36) NOT NULL REFERENCES ledger.provenance_record(id) ON DELETE RESTRICT,
    chain varchar(32) NOT NULL,
    adapter varchar(32) NOT NULL CHECK (adapter = 'offline_fixture'),
    receipt_id varchar(256) NOT NULL,
    transaction_id varchar(256) NOT NULL,
    accepted integer NOT NULL CHECK (accepted IN (0,1)),
    created_at varchar(32) NOT NULL,
    CONSTRAINT uq_provenance_receipt UNIQUE (chain, adapter, receipt_id),
    CONSTRAINT uq_provenance_transaction UNIQUE (chain, adapter, transaction_id)
);
CREATE INDEX IF NOT EXISTS ix_provenance_owner ON ledger.provenance_record
    (issuer, subject, tenant_id, project_id, created_at);
CREATE INDEX IF NOT EXISTS ix_provenance_queue_status ON ledger.provenance_queue (status, updated_at);

CREATE OR REPLACE FUNCTION ledger.provenance_evidence_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'provenance evidence cannot be deleted';
    END IF;
    IF NEW.id <> OLD.id OR NEW.issuer <> OLD.issuer OR NEW.subject <> OLD.subject
       OR NEW.tenant_id <> OLD.tenant_id OR NEW.project_id <> OLD.project_id
       OR NEW.idempotency_key <> OLD.idempotency_key OR NEW.request_hash <> OLD.request_hash
       OR NEW.content_hash <> OLD.content_hash OR NEW.evidence::text <> OLD.evidence::text
       OR NEW.source::text <> OLD.source::text OR NEW.created_at <> OLD.created_at
       OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'immutable provenance content or non-monotonic version';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS provenance_evidence_immutable ON ledger.provenance_record;
CREATE TRIGGER provenance_evidence_immutable BEFORE UPDATE OR DELETE ON ledger.provenance_record
FOR EACH ROW EXECUTE FUNCTION ledger.provenance_evidence_immutable();

CREATE OR REPLACE FUNCTION ledger.provenance_audit_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'provenance audit is append-only';
END;
$$;
DROP TRIGGER IF EXISTS provenance_events_append_only ON ledger.provenance_event;
CREATE TRIGGER provenance_events_append_only BEFORE UPDATE OR DELETE ON ledger.provenance_event
FOR EACH ROW EXECUTE FUNCTION ledger.provenance_audit_append_only();
DROP TRIGGER IF EXISTS provenance_receipts_append_only ON ledger.provenance_receipt;
CREATE TRIGGER provenance_receipts_append_only BEFORE UPDATE OR DELETE ON ledger.provenance_receipt
FOR EACH ROW EXECUTE FUNCTION ledger.provenance_audit_append_only();

COMMENT ON TABLE ledger.provenance_record IS
    'Private retained evidence metadata. local_only/offline_fixture are never onchain confirmation.';
COMMENT ON TABLE ledger.provenance_queue IS
    'Durable operator review queue, no automatic broadcaster or chain worker.';
COMMIT;
