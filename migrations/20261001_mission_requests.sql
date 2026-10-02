-- Review-only migration. Run only in a separately authorized deployment window.
BEGIN;
CREATE SCHEMA IF NOT EXISTS mission;
REVOKE ALL ON SCHEMA mission FROM PUBLIC;
CREATE TABLE IF NOT EXISTS mission.request (
 id uuid PRIMARY KEY, issuer text NOT NULL, owner_subject uuid NOT NULL,
 idempotency_key uuid NOT NULL, payload jsonb NOT NULL, payload_sha256 text NOT NULL,
 state text NOT NULL DEFAULT 'submitted' CHECK (state IN ('submitted','cancelled')),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(issuer,owner_subject,idempotency_key)
);
CREATE INDEX IF NOT EXISTS mission_request_owner_time ON mission.request(issuer,owner_subject,created_at DESC);
CREATE TABLE IF NOT EXISTS mission.quote (
 id uuid PRIMARY KEY, request_id uuid NOT NULL REFERENCES mission.request(id),
 revision integer NOT NULL CHECK(revision>0), amount_minor bigint NOT NULL CHECK(amount_minor BETWEEN 1 AND 100000000),
 currency text NOT NULL CHECK(currency='usd'), scope text NOT NULL,
 expires_at timestamptz NOT NULL, approved_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(request_id,revision), UNIQUE(id,request_id)
);
CREATE TABLE IF NOT EXISTS mission.payment_attempt (
 id uuid PRIMARY KEY, request_id uuid NOT NULL REFERENCES mission.request(id),
 quote_id uuid NOT NULL UNIQUE REFERENCES mission.quote(id),
 provider_session text UNIQUE, payment_intent text UNIQUE,
 amount_minor bigint NOT NULL CHECK(amount_minor BETWEEN 1 AND 100000000), currency text NOT NULL CHECK(currency='usd'),
 state text NOT NULL DEFAULT 'reserved' CHECK(state IN ('reserved','checkout','paid','failed','expired','partially_refunded','refunded')),
 refunded_minor bigint NOT NULL DEFAULT 0 CHECK(refunded_minor BETWEEN 0 AND amount_minor), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(quote_id,request_id) REFERENCES mission.quote(id,request_id)
);
CREATE TABLE IF NOT EXISTS mission.payment_event (
 event_id text PRIMARY KEY, attempt_id uuid NOT NULL REFERENCES mission.payment_attempt(id),
 payload_sha256 text NOT NULL, event_type text NOT NULL, received_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON ALL TABLES IN SCHEMA mission FROM PUBLIC;
-- Grant the reviewed MINDEX service role explicitly at deployment; no guessed role grant here.
COMMIT;
