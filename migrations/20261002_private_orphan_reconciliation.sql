-- Additive crash-gap repair for private archive object versions whose worker died
-- before it committed the immutable object reference to retention.artifact.
BEGIN;
ALTER TABLE retention.artifact
    ADD COLUMN IF NOT EXISTS archive_reconciled_at timestamptz;
COMMIT;
