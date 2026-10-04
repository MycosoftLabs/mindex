-- Source-attested publication/taxon evidence, separate from query-derived hits
-- and from reviewed links in bio.publication_taxon.
-- Cursor-owned migration apply; this file has not been applied to any database.

BEGIN;

CREATE TABLE IF NOT EXISTS bio.publication_taxon_evidence (
    evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    publication_id VARCHAR(64) NOT NULL REFERENCES core.publications(id) ON DELETE CASCADE,
    taxon_id UUID NOT NULL REFERENCES core.taxon(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_taxon_id TEXT NOT NULL,
    provider_source_record_id TEXT NOT NULL,
    provider_source_url TEXT NOT NULL,
    association_method TEXT NOT NULL,
    evidence_state TEXT NOT NULL DEFAULT 'candidate_source_attested',
    source_content_sha256 TEXT NOT NULL,
    normalization_sha256 TEXT NOT NULL,
    license TEXT,
    attribution TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT publication_taxon_evidence_method_check
        CHECK (association_method IN ('exact_genbank_taxon_record_reference')),
    CONSTRAINT publication_taxon_evidence_state_check
        CHECK (evidence_state IN ('candidate_source_attested', 'accepted_source_attested', 'rejected')),
    CONSTRAINT publication_taxon_evidence_hash_check
        CHECK (source_content_sha256 ~ '^[0-9a-f]{64}$' AND normalization_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT publication_taxon_evidence_source_identity_unique
        UNIQUE (provider, provider_source_record_id, source_content_sha256, taxon_id)
);

CREATE INDEX IF NOT EXISTS idx_publication_taxon_evidence_taxon
    ON bio.publication_taxon_evidence (taxon_id, evidence_state, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_publication_taxon_evidence_publication
    ON bio.publication_taxon_evidence (publication_id, taxon_id);

GRANT SELECT, INSERT, UPDATE ON bio.publication_taxon_evidence TO mindex;

CREATE OR REPLACE FUNCTION bio.guard_publication_taxon_evidence_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF ROW(
        NEW.publication_id, NEW.taxon_id, NEW.provider, NEW.provider_taxon_id,
        NEW.provider_source_record_id, NEW.provider_source_url, NEW.association_method,
        NEW.source_content_sha256, NEW.normalization_sha256, NEW.license,
        NEW.attribution, NEW.recorded_at, NEW.metadata
    ) IS DISTINCT FROM ROW(
        OLD.publication_id, OLD.taxon_id, OLD.provider, OLD.provider_taxon_id,
        OLD.provider_source_record_id, OLD.provider_source_url, OLD.association_method,
        OLD.source_content_sha256, OLD.normalization_sha256, OLD.license,
        OLD.attribution, OLD.recorded_at, OLD.metadata
    ) THEN
        RAISE EXCEPTION 'publication taxon evidence source fields are immutable';
    END IF;
    IF OLD.evidence_state <> 'candidate_source_attested'
       OR NEW.evidence_state NOT IN ('candidate_source_attested', 'accepted_source_attested', 'rejected') THEN
        RAISE EXCEPTION 'publication taxon evidence review state is terminal';
    END IF;
    IF NEW.evidence_state <> OLD.evidence_state AND NULLIF(BTRIM(NEW.reviewed_by), '') IS NULL THEN
        RAISE EXCEPTION 'a reviewer identity is required for evidence disposition';
    END IF;
    IF NEW.evidence_state = OLD.evidence_state AND NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
        RAISE EXCEPTION 'reviewer identity changes require an evidence disposition';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_publication_taxon_evidence_immutable ON bio.publication_taxon_evidence;
CREATE TRIGGER trg_publication_taxon_evidence_immutable
BEFORE UPDATE ON bio.publication_taxon_evidence
FOR EACH ROW EXECUTE FUNCTION bio.guard_publication_taxon_evidence_immutability();

COMMENT ON TABLE bio.publication_taxon_evidence IS
    'Immutable provider evidence candidates for publication/taxon association; only reviewed accepted evidence may be promoted to bio.publication_taxon.';
COMMENT ON COLUMN bio.publication_taxon_evidence.provider_taxon_id IS
    'Exact provider taxon identifier, resolved to taxon_id through a unique source crosswalk.';
COMMENT ON COLUMN bio.publication_taxon_evidence.provider_source_record_id IS
    'Exact accession/version/reference locator or other provider record identity; not a name-search result.';
COMMENT ON COLUMN bio.publication_taxon_evidence.source_content_sha256 IS
    'SHA-256 of the exact decoded provider record bytes used for normalization.';
COMMENT ON COLUMN bio.publication_taxon_evidence.normalization_sha256 IS
    'SHA-256 of the canonical normalized publication-reference payload.';

COMMIT;
