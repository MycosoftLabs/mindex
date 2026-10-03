-- SOURCE ONLY. Cursor must qualify/apply separately; no token_attempt or core writes.
-- This registry stores source-reported associations, never chain confirmation.
BEGIN;

CREATE TABLE fungip.first40_source_batch (
  snapshot_sha256 text PRIMARY KEY CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  schema_version smallint NOT NULL CHECK (schema_version = 1),
  catalog_sha256 text NOT NULL CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$'),
  launch_sha256 text NOT NULL CHECK (launch_sha256 ~ '^[0-9a-f]{64}$'),
  handoff_sha256 text NOT NULL CHECK (handoff_sha256 ~ '^[0-9a-f]{64}$'),
  correction_sha256 text NOT NULL CHECK (correction_sha256 ~ '^[0-9a-f]{64}$'),
  corrected_input_version text NOT NULL CHECK (corrected_input_version = 'direct_user_correction_v1'),
  correction_recorded_at_utc timestamptz NOT NULL,
  authority_approval_reference text NOT NULL CHECK (length(btrim(authority_approval_reference)) > 0),
  launch_schema text NOT NULL CHECK (launch_schema = 'first40_launch_log_v1'),
  superseded_encoding text NOT NULL CHECK (superseded_encoding IN ('json','semicolon')),
  authority jsonb NOT NULL,
  source_reported_as_of_utc timestamptz NOT NULL,
  source_reported_as_of_pt text NOT NULL CHECK (length(btrim(source_reported_as_of_pt)) > 0),
  verification_basis text NOT NULL DEFAULT 'user_supplied_and_attached_verification'
    CHECK (verification_basis = 'user_supplied_and_attached_verification'),
  new_chain_verified boolean NOT NULL DEFAULT false CHECK (new_chain_verified = false),
  admitted_at timestamptz NOT NULL DEFAULT now(),
  CHECK (authority = jsonb_build_object(
    'approved',true,'approval_reference',authority_approval_reference,'launch_schema',launch_schema,
    'superseded_encoding',superseded_encoding,'correction_schema',corrected_input_version,
    'source_bindings',jsonb_build_object('catalog_sha256',catalog_sha256,
      'launch_sha256',launch_sha256,'handoff_sha256',handoff_sha256,'correction_sha256',correction_sha256)))
);

CREATE TABLE fungip.first40_launch_association (
  species_id text PRIMARY KEY REFERENCES fungip.species(species_id) ON DELETE RESTRICT
    CHECK (species_id ~ '^FG(00[1-9]|0[1-3][0-9]|040)$'),
  snapshot_sha256 text NOT NULL REFERENCES fungip.first40_source_batch(snapshot_sha256) ON DELETE RESTRICT,
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  ticker text NOT NULL CHECK (length(ticker) > 0),
  accepted_name text NOT NULL CHECK (length(accepted_name) > 0),
  dna_sha256 text NOT NULL CHECK (dna_sha256 ~ '^[0-9a-f]{64}$'),
  dna_accession_version text NOT NULL CHECK (dna_accession_version ~ '^[A-Z]+_?[0-9]+\.[0-9]+$'),
  dna_database text NOT NULL,
  dna_source_url text NOT NULL,
  image_credit text NOT NULL CHECK (length(image_credit) > 0),
  image_license text NOT NULL CHECK (length(image_license) > 0),
  image_sha256 text NOT NULL CHECK (image_sha256 ~ '^[0-9a-f]{64}$'),
  launch_status text NOT NULL CHECK (launch_status = 'verified'),
  source_hash_match boolean NOT NULL CHECK (source_hash_match = true),
  canonical_approved boolean NOT NULL CHECK (canonical_approved = true),
  owner_entity text NOT NULL DEFAULT 'MycoDAO' CHECK (owner_entity = 'MycoDAO'),
  mint_address text UNIQUE CHECK (mint_address ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
  launch_tx text UNIQUE CHECK (launch_tx ~ '^[1-9A-HJ-NP-Za-km-z]{64,88}$'),
  launched_at timestamptz,
  launched_at_pt text,
  solana_explorer_url text,
  solana_explorer_tx_url text,
  solscan_tx_url text,
  solscan_token_url text,
  usepaid_url text,
  usepaid_short_url text,
  pumpfun_url text,
  metadata_uri text,
  recipient text,
  token_program text CHECK (token_program ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'),
  decimals smallint CHECK (decimals BETWEEN 0 AND 255),
  supply_raw text CHECK (supply_raw ~ '^[0-9]+$'),
  candidate_launch jsonb,
  superseded_mints text[] NOT NULL DEFAULT ARRAY[]::text[] CHECK (array_position(superseded_mints,NULL) IS NULL),
  synonyms text[] NOT NULL DEFAULT ARRAY[]::text[],
  catalog_review_flags text[] NOT NULL DEFAULT ARRAY[]::text[] CHECK (array_position(catalog_review_flags,NULL) IS NULL),
  gap_flags text[] NOT NULL DEFAULT ARRAY[]::text[] CHECK (array_position(gap_flags,NULL) IS NULL),
  verification_basis text NOT NULL DEFAULT 'user_supplied_and_attached_verification'
    CHECK (verification_basis = 'user_supplied_and_attached_verification'),
  new_chain_verified boolean NOT NULL DEFAULT false CHECK (new_chain_verified = false),
  imported_at timestamptz NOT NULL DEFAULT now(),
  CHECK (mint_address IS NOT NULL),
  CHECK (species_id = 'FG026' OR launch_tx IS NOT NULL),
  CHECK (species_id <> 'FG026' OR (num_nonnulls(launch_tx,launched_at,launched_at_pt,
    solana_explorer_tx_url,solscan_tx_url,solscan_token_url,usepaid_short_url,token_program) = 0
    AND decimals IS NOT NULL AND decimals = 6
    AND supply_raw IS NOT NULL AND supply_raw = '1000000000000000')),
  CHECK ((species_id = 'FG034' AND synonyms = ARRAY['Ganoderma lingzhi']::text[])
    OR (species_id <> 'FG034' AND synonyms = ARRAY[]::text[])),
  CHECK (candidate_launch IS NULL)
);

CREATE FUNCTION fungip.first40_prevent_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'First40 source associations are immutable; explicit future curation is required';
END $$;
CREATE TRIGGER fungip_first40_batch_immutable BEFORE UPDATE OR DELETE ON fungip.first40_source_batch
  FOR EACH ROW EXECUTE FUNCTION fungip.first40_prevent_mutation();
CREATE TRIGGER fungip_first40_association_immutable BEFORE UPDATE OR DELETE ON fungip.first40_launch_association
  FOR EACH ROW EXECUTE FUNCTION fungip.first40_prevent_mutation();

CREATE FUNCTION fungip.first40_guard_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  parent fungip.species%ROWTYPE;
  incoming_mints text[];
  incoming_txs text[];
BEGIN
  -- Shared with the existing catalog importer, not an issuance/ledger lock.
  -- A waiting repeatable-read snapshot could miss another committed retired mint.
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'First40 registry inserts require READ COMMITTED lock visibility';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtext('fungip.catalog.import'));
  SELECT * INTO parent FROM fungip.species WHERE species_id = NEW.species_id FOR SHARE;
  IF NOT FOUND OR parent.sequence_valid IS DISTINCT FROM true OR parent.image_valid IS DISTINCT FROM true
    OR parent.accepted_name IS DISTINCT FROM NEW.accepted_name
    OR parent.record->>'accepted_name' IS DISTINCT FROM NEW.accepted_name
    OR parent.record->>'ticker' IS DISTINCT FROM NEW.ticker
    OR parent.record->'dna'->>'sha256' IS DISTINCT FROM NEW.dna_sha256
    OR parent.record->'dna'->>'accession_version' IS DISTINCT FROM NEW.dna_accession_version
    OR parent.record->'image'->>'attribution' IS DISTINCT FROM NEW.image_credit
    OR parent.record->'image'->>'license_code' IS DISTINCT FROM NEW.image_license
    OR parent.record->'image'->>'sha256' IS DISTINCT FROM NEW.image_sha256 THEN
    RAISE EXCEPTION 'First40 science/image parent qualification mismatch';
  END IF;
  IF NEW.candidate_launch IS NOT NULL THEN
    RAISE EXCEPTION 'Corrected first40 associations have no pending candidate';
  END IF;
  incoming_mints := array_remove(ARRAY[NEW.mint_address] || NEW.superseded_mints,NULL);
  incoming_txs := array_remove(ARRAY[NEW.launch_tx],NULL);
  IF EXISTS (SELECT 1 FROM unnest(incoming_mints) item WHERE item !~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$')
    OR cardinality(incoming_mints) <> (SELECT count(DISTINCT item) FROM unnest(incoming_mints) item)
    OR cardinality(incoming_txs) <> (SELECT count(DISTINCT item) FROM unnest(incoming_txs) item) THEN
    RAISE EXCEPTION 'First40 duplicate or malformed canonical/candidate/superseded identity';
  END IF;
  IF EXISTS (SELECT 1 FROM fungip.first40_launch_association existing WHERE
    incoming_mints && array_remove(ARRAY[existing.mint_address,existing.candidate_launch->>'mint_address']
      || existing.superseded_mints,NULL)
    OR incoming_txs && array_remove(ARRAY[existing.launch_tx,existing.candidate_launch->>'launch_tx'],NULL)) THEN
    RAISE EXCEPTION 'First40 identity collides with an existing source association';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER fungip_first40_association_insert_guard BEFORE INSERT ON fungip.first40_launch_association
  FOR EACH ROW EXECUTE FUNCTION fungip.first40_guard_insert();
-- No grants: future Cursor qualification must allocate controlled collection writes.
-- No updates/deletes, parent/core mutations, wallet action, or token confirmation here.
COMMIT;
