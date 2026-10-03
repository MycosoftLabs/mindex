-- Cursor applies only after schema qualification and backup; additive collection scope.
BEGIN;
CREATE SCHEMA IF NOT EXISTS fungip;
CREATE TABLE IF NOT EXISTS fungip.import_run (
  catalog_sha256 text PRIMARY KEY CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$'),
  manifest jsonb NOT NULL, imported_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fungip.species (
  species_id text PRIMARY KEY CHECK (species_id ~ '^FG[0-9]{3}$'),
  taxon_id uuid UNIQUE REFERENCES core.taxon(id) ON DELETE RESTRICT,
  accepted_name text NOT NULL,
  record jsonb NOT NULL,
  external_ids jsonb NOT NULL,
  verified_its_sequence text,
  image_valid boolean NOT NULL DEFAULT false,
  sequence_valid boolean NOT NULL DEFAULT false,
  missing_data_flags jsonb NOT NULL,
  validation_errors jsonb NOT NULL,
  record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
  catalog_sha256 text NOT NULL REFERENCES fungip.import_run(catalog_sha256),
  resolution_status text NOT NULL CHECK (resolution_status IN ('unresolved','resolved','identity_conflict','source_conflict','duplicate_canonical')),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fungip.import_observation (
  observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_sha256 text NOT NULL REFERENCES fungip.import_run(catalog_sha256),
  report jsonb NOT NULL, observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fungip.species_revision (
  species_id text NOT NULL REFERENCES fungip.species(species_id),
  record_sha256 text NOT NULL, record jsonb NOT NULL,
  catalog_sha256 text NOT NULL REFERENCES fungip.import_run(catalog_sha256),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(species_id, record_sha256)
);
CREATE TABLE IF NOT EXISTS fungip.page_verification (
  species_id text PRIMARY KEY REFERENCES fungip.species(species_id),
  taxon_id uuid NOT NULL REFERENCES core.taxon(id),
  record_sha256 text NOT NULL,
  canonical_url text NOT NULL,
  reviewer text NOT NULL, reviewed_at timestamptz NOT NULL,
  evidence jsonb NOT NULL,
  CHECK (canonical_url = 'https://mycosoft.com/natureos/ancestry/species/' || taxon_id::text)
);
CREATE TABLE IF NOT EXISTS fungip.token_attempt (
  attempt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  species_id text NOT NULL REFERENCES fungip.species(species_id),
  network text NOT NULL CHECK (network IN ('solana-mainnet-beta','solana-devnet','solana-testnet')),
  status text NOT NULL CHECK (status IN ('draft','prepared','submitted-unknown','confirmed','failed')),
  mint_address text, transaction_signature text,
  transaction_url text, metadata_url text, usepaid_url text,
  intended_recipient_handle text NOT NULL DEFAULT 'nodefather' CHECK (intended_recipient_handle='nodefather'),
  recipient_identity jsonb, recipient_public_wallet text,
  prepared_payload jsonb, prepared_payload_sha256 text, prepared_at timestamptz,
  finalized_evidence jsonb,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'confirmed' OR (mint_address IS NOT NULL AND transaction_signature IS NOT NULL AND finalized_evidence IS NOT NULL)),
  CHECK (status NOT IN ('submitted-unknown','confirmed') OR transaction_signature IS NOT NULL),
  CHECK (status NOT IN ('prepared','submitted-unknown','confirmed') OR (prepared_payload IS NOT NULL AND prepared_payload_sha256 IS NOT NULL AND prepared_at IS NOT NULL)),
  UNIQUE(network, mint_address), UNIQUE(network, transaction_signature)
);
-- Unknown outcomes and confirmed species prevent a second active launch.
CREATE UNIQUE INDEX IF NOT EXISTS fungip_one_launch_per_species_network
  ON fungip.token_attempt(species_id, network) WHERE status <> 'failed';
CREATE TABLE IF NOT EXISTS fungip.token_event (
  event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  attempt_id uuid NOT NULL REFERENCES fungip.token_attempt(attempt_id),
  previous_status text, status text NOT NULL, evidence jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION fungip.prevent_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'FungiP token events are append-only'; END $$;
DROP TRIGGER IF EXISTS fungip_token_events_immutable ON fungip.token_event;
CREATE TRIGGER fungip_token_events_immutable BEFORE UPDATE OR DELETE ON fungip.token_event
  FOR EACH ROW EXECUTE FUNCTION fungip.prevent_event_mutation();
CREATE OR REPLACE FUNCTION fungip.guard_attempt_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.species_id <> OLD.species_id OR NEW.network <> OLD.network THEN
    RAISE EXCEPTION 'Attempt species and network are permanent';
  END IF;
  IF OLD.prepared_payload IS NOT NULL AND (NEW.prepared_payload IS DISTINCT FROM OLD.prepared_payload OR NEW.prepared_payload_sha256 IS DISTINCT FROM OLD.prepared_payload_sha256) THEN
    RAISE EXCEPTION 'Prepared species envelope is immutable';
  END IF;
  IF OLD.transaction_signature IS NOT NULL AND NEW.transaction_signature IS DISTINCT FROM OLD.transaction_signature THEN
    RAISE EXCEPTION 'Reconcile original signature; do not replace it';
  END IF;
  IF OLD.mint_address IS NOT NULL AND NEW.mint_address IS DISTINCT FROM OLD.mint_address THEN
    RAISE EXCEPTION 'Mint identity cannot be replaced';
  END IF;
  IF NEW.status <> OLD.status AND NOT (
    (OLD.status='draft' AND NEW.status IN ('prepared','failed')) OR
    (OLD.status='prepared' AND NEW.status IN ('submitted-unknown','failed')) OR
    (OLD.status='submitted-unknown' AND NEW.status IN ('confirmed','failed'))
  ) THEN RAISE EXCEPTION 'Invalid attempt transition'; END IF;
  IF OLD.status='submitted-unknown' AND NEW.status <> OLD.status THEN
    IF NEW.finalized_evidence IS NULL OR
       NEW.finalized_evidence->>'confirmation_status' IS DISTINCT FROM 'finalized' OR
       NEW.finalized_evidence->>'transaction_signature' IS DISTINCT FROM OLD.transaction_signature OR
       NEW.finalized_evidence->>'network' IS DISTINCT FROM OLD.network OR
       NEW.finalized_evidence->>'verifier' IS DISTINCT FROM 'fungip-solana-metaplex-v1' THEN
      RAISE EXCEPTION 'Unknown outcome requires exact finalized receipt';
    END IF;
    IF NOT (NEW.finalized_evidence ? 'transaction_error') OR
       (NEW.status='confirmed') <> (NEW.finalized_evidence->'transaction_error' = 'null'::jsonb) THEN
      RAISE EXCEPTION 'Finalized receipt result mismatch';
    END IF;
    IF NEW.status='confirmed' AND (NEW.finalized_evidence->>'prepared_payload_sha256' IS DISTINCT FROM NEW.prepared_payload_sha256 OR
       NEW.finalized_evidence->'species_binding' IS DISTINCT FROM NEW.prepared_payload->'metadata_binding' OR
       NEW.finalized_evidence->'species_binding'->>'species_id' IS DISTINCT FROM NEW.species_id) THEN
      RAISE EXCEPTION 'Confirmed receipt must bind immutable prepared species metadata';
    END IF;
    -- Creation identity is transaction-time evidence. A later mutable account read
    -- (including minContextSlot) must never substitute for these decoded fields.
    IF NEW.status='confirmed' AND (
       NEW.finalized_evidence->'creation_metadata'->>'decoder' IS DISTINCT FROM 'metaplex-create-metadata-account-v3-borsh-353d01be' OR
       NEW.finalized_evidence->'creation_metadata'->>'name' IS DISTINCT FROM NEW.prepared_payload->>'name' OR
       NEW.finalized_evidence->'creation_metadata'->>'symbol' IS DISTINCT FROM NEW.prepared_payload->>'symbol' OR
       NEW.finalized_evidence->'creation_metadata'->>'uri' IS DISTINCT FROM NEW.prepared_payload->>'metadata_uri' OR
       NEW.finalized_evidence->'creation_metadata'->>'mint_address' IS DISTINCT FROM NEW.mint_address OR
       NEW.finalized_evidence->'current_metadata_account'->>'name' IS DISTINCT FROM NEW.prepared_payload->>'name' OR
       NEW.finalized_evidence->'current_metadata_account'->>'symbol' IS DISTINCT FROM NEW.prepared_payload->>'symbol' OR
       NEW.finalized_evidence->'current_metadata_account'->>'uri' IS DISTINCT FROM NEW.prepared_payload->>'metadata_uri') THEN
      RAISE EXCEPTION 'Confirmed receipt requires exact supported creation and current identities';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS fungip_attempt_transition_guard ON fungip.token_attempt;
CREATE TRIGGER fungip_attempt_transition_guard BEFORE UPDATE ON fungip.token_attempt
  FOR EACH ROW EXECUTE FUNCTION fungip.guard_attempt_transition();
-- No grants here: Cursor must assign SELECT to existing read-role and collection writes
-- only to the controlled importer/receipt operator, never a public website role.
COMMIT;
