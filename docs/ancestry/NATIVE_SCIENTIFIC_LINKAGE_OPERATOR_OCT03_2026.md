# Native scientific linkage operator notes — 2026-10-03

This is a portable review packet for the MINDEX owner and Cursor. Its SQL preflight and database staging instructions have not been executed. The source captures cited below were read-only provider GETs. It grants no database apply, migration, ETL database write, AWS, production, or deployment authorization.

## 1. Read-only qualification

Record service origin/environment, deployed commit or image digest, route version, timestamp (UTC), DB/schema identity and caller role before interpreting a result. Use an existing read-only database identity. Bind the exact UUID as a parameter; do not paste secrets into shell commands or report output. If any schema or permission check fails, record `unavailable` and stop rather than interpreting zero rows.

The following is a PostgreSQL preflight template. Verify the deployed service's actual schema names and required columns first; it has not been run here.

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';
SELECT current_database(), current_schema(), current_setting('transaction_read_only');
SELECT expected.name, to_regclass(expected.name) AS relation
FROM (VALUES
  ('core.taxon'), ('core.taxon_external_id'), ('bio.genetic_sequence'),
  ('bio.genome'), ('bio.compound'), ('bio.taxon_compound'),
  ('bio.publication_taxon'), ('core.publications'),
  ('bio.taxon_interaction'), ('media.image'), ('media.video'), ('media.audio')
) AS expected(name);
SELECT table_schema, table_name, column_name, data_type
FROM information_schema.columns
WHERE (table_schema, table_name) IN (
  ('core', 'taxon'), ('core', 'taxon_external_id'),
  ('bio', 'genetic_sequence'), ('bio', 'genome'),
  ('bio', 'compound'), ('bio', 'taxon_compound'),
  ('bio', 'publication_taxon'), ('core', 'publications'),
  ('bio', 'taxon_interaction'), ('media', 'image'),
  ('media', 'video'), ('media', 'audio')
)
ORDER BY table_schema, table_name, ordinal_position;
ROLLBACK;
```

After the schema receipt, run separately bounded queries in independent read-only transactions. Keep selectors separate: `$1 = '5334'` is the NCBI taxid for *Schizophyllum commune*; `$2 = '6db28640-67fb-4808-90de-956a856366f7'` is the expected canonical MINDEX UUID. A UUID is never an NCBI `external_id`, and a taxid is never cast to `uuid`.

```sql
-- Positive control: exact source taxid, joined to canonical identity for consistency.
SELECT x.source, x.external_id, x.taxon_id, t.canonical_name, t.rank, x.created_at
FROM core.taxon_external_id AS x
JOIN core.taxon AS t ON t.id = x.taxon_id
WHERE x.source = 'ncbi' AND x.external_id = $1
ORDER BY x.taxon_id
LIMIT 3;

-- Negative control: NCBI taxid 5322 is Pleurotus ostreatus, not splitgill.
-- Zero rows means unlinked under this exact source key, not absent source data.
SELECT x.source, x.external_id, x.taxon_id, t.canonical_name, t.rank
FROM core.taxon_external_id AS x
JOIN core.taxon AS t ON t.id = x.taxon_id
WHERE x.source = 'ncbi' AND x.external_id = '5322'
ORDER BY x.taxon_id
LIMIT 3;

-- Stored sequences and linkage state; never export sequence bodies for this check.
SELECT id, accession, version, taxon_id, source, source_url, gene,
       region, sequence_type, sequence_length,
       metadata->'source_taxon_ids' AS source_taxon_ids,
       metadata->'taxon_linkage' AS taxon_linkage
FROM bio.genetic_sequence
WHERE taxon_id = $2::uuid
ORDER BY accession
LIMIT 8;

-- Stored assemblies are separate from deposited nucleotide sequences.
SELECT id, taxon_id, source, accession, assembly_level, release_date
FROM bio.genome
WHERE taxon_id = $2::uuid
ORDER BY release_date DESC NULLS LAST, accession
LIMIT 8;

-- Exact species-compound assertions with evidence and citations.
SELECT tc.taxon_id, tc.compound_id, c.name, c.pubchem_id,
       tc.relationship_type, tc.evidence_level, tc.tissue_location,
       tc.source, tc.source_url, tc.doi
FROM bio.taxon_compound tc
JOIN bio.compound c ON c.id = tc.compound_id
WHERE tc.taxon_id = $2::uuid
ORDER BY tc.compound_id
LIMIT 8;

-- Exact publication links. Current link schema does not record its evidence source.
SELECT pt.publication_id, pt.taxon_id, pt.relevance_score,
       p.doi, p.source, p.url
FROM bio.publication_taxon pt
JOIN core.publications p ON p.id = pt.publication_id
WHERE pt.taxon_id = $2::uuid
ORDER BY pt.relevance_score DESC NULLS LAST, pt.publication_id
LIMIT 8;

-- Both directions are relevant; keep source and target intact.
SELECT id, source_taxon_id, target_taxon_id, interaction_type,
       evidence_source, evidence_url
FROM bio.taxon_interaction
WHERE source_taxon_id = $2::uuid OR target_taxon_id = $2::uuid
ORDER BY id
LIMIT 8;
```

For each crosswalk query, record exact row count, UUID, canonical name, and rank. Taxid 5334 is a positive control only when it has exactly one crosswalk to UUID `$2`, with canonical name *Schizophyllum commune* and the expected rank. Taxid 5322 must never resolve to the splitgill UUID; if it has a unique link, it should identify *Pleurotus ostreatus*. Zero means unlinked under that exact source key, not absent data in NCBI; two or more taxon UUIDs is ambiguous. A UUID mistakenly used as `external_id` should return no NCBI crosswalk and is a selector error, not evidence of absent source data. Do not resolve any mismatch, zero, or ambiguity by name search, genus, fuzzy similarity, first row, or provider search result.

## 2. Finite GenBank linkage backfill proposal

No schema change is needed to store the exact link: `migrations/0012_genetics.sql` already defines nullable `bio.genetic_sequence.taxon_id`, `version`, and `metadata`. The new ETL records source taxon IDs and linkage state for future accepted GenBank rows. Existing rows whose source taxon ID is not already retained cannot be safely backfilled by this patch. An operator must supply a finite, reviewed staging list with one row per accession: exact accession.version, exact numeric NCBI taxid from the source record, expected current UUID (or NULL), approved target UUID, reviewer, and evidence URL/hash. Do not derive that list with species-name matching.

Before a proposed apply, the read-only preflight must prove:

- The staged accession exists exactly once and belongs to source `genbank`.
- The stored version/source URL match the reviewed NCBI record.
- The staged source taxid maps through `core.taxon_external_id(source='ncbi', external_id=...)` to exactly one UUID.
- That UUID equals the proposed target and the target has independently checked canonical name/rank.
- The current row's taxon UUID equals the staged expected-current value, including NULL when appropriate.
- No accession appears twice in the stage and the expected update count is finite and reviewed.

The apply is a single transaction with row-level before/after capture and no auto-commit. The operator checks every returned accession, source taxid, old UUID and new UUID against the reviewed stage, then explicitly commits or rolls back. Save the exact before image of each changed row (at minimum `id`, `accession`, `taxon_id`, `metadata`, `updated_at`) in the controlled change record before commit. Rollback uses only that saved finite row set and compare-and-set conditions (`current taxon_id` still equals the applied target); if any row has since changed, stop and re-review. This packet does not include an executable apply statement because there is no owner-approved stage or live schema receipt.

## 3. Publication-link evidence staging contract

Current `bio.publication_taxon` contains only publication ID, taxon UUID, relevance score and creation time. Query-derived literature remains outside that link table. A candidate link must cite an exact source record and canonical taxon identity; title/name search overlap alone is insufficient.

The successor candidate includes the additive migration `migrations/20261003_publication_taxon_evidence_OCT03_2026.sql`, which creates `bio.publication_taxon_evidence` separately from reviewed links in `bio.publication_taxon`. It records provider/taxon identity, source locator/URL, method/state, source and normalized payload SHA-256 values, and optional rights/reviewer fields. Existing links are not upgraded. The migration and importer have not been applied or run against a database.

### Bounded source-attested GenBank reference staging

The exact NCBI record `PZ955173.1` was fetched through EFetch and pinned at 5,730 decoded UTF-8 bytes, SHA-256 `bdc407ee4e1bf28042c4ee5395825c0b20014bff96dad2a33c3b9c558e5cccc2`. It identifies organism *Schizophyllum commune*, source taxid `5334`, and one titled, in-press reference at locator `PZ955173.1#reference=1`; the separate `Direct Submission` reference is excluded. The titled source reference has no PubMed ID or DOI in this record, so it stays a candidate and is not resolved by title search.

After Cursor applies and validates the additive migration in a controlled staging environment, the bounded call is:

```python
from mindex_etl.db import get_connection
from mindex_etl.jobs.import_taxon_publication_evidence import (
    fetch_and_stage_genbank_publication_evidence,
)

conn = get_connection()
try:
    receipt = fetch_and_stage_genbank_publication_evidence(
        conn,
        "PZ955173.1",
        expected_source_sha256=(
            "bdc407ee4e1bf28042c4ee5395825c0b20014bff96dad2a33c3b9c558e5cccc2"
        ),
    )
    print(receipt)
    if receipt.get("state") == "candidate_source_attested" and receipt.get("staged") == 1:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT publication_id, taxon_id, provider, provider_taxon_id,
                          provider_source_record_id, source_content_sha256,
                          normalization_sha256, evidence_state
                   FROM bio.publication_taxon_evidence
                   WHERE taxon_id = %s AND provider_source_record_id = %s
                     AND source_content_sha256 = %s""",
                (receipt["taxon_id"], "PZ955173.1#reference=1",
                 "bdc407ee4e1bf28042c4ee5395825c0b20014bff96dad2a33c3b9c558e5cccc2"),
            )
            print(cur.fetchall())
    # After human review, choose exactly one explicit action:
    # conn.commit()
    # conn.rollback()
finally:
    conn.close()  # Uncommitted work rolls back on close.
```

The importer rejects source-hash changes, accession.version mismatches, missing/multiple NCBI taxids, missing source organism, absent/ambiguous exact `core.taxon_external_id` crosswalks, and canonical-name mismatches. Require receipt state `candidate_source_attested` and one staged row, then inspect the exact evidence row in the same transaction. Commit only after owner review; otherwise roll back. The importer never creates a `bio.publication_taxon` link, schedules work, or qualifies a deployed environment.

If any stage fails before commit, call `conn.rollback()` or close the connection; both the content-addressed `core.publications` candidate and evidence row are in the same caller-owned transaction. After commit, preserve the evidence rows and mark a reviewed disposition `rejected` rather than deleting provenance. A migration rollback may drop the new empty evidence table only after a same-environment count confirms zero evidence rows and owner review confirms no dependent records; once evidence exists, use a data-preserving forward repair.

Compound-link backfills have the same evidence requirement. The existing name-overlap job must not be used for this species repair. Genomics assembly association requires an actual assembly record and its taxon identifier; a GenBank marker accession is not an assembly.

## 4. Evidence receipt shape

Return one owner-attributed receipt per environment with:

```text
timestamp_utc, service_origin/environment, deployed_commit_or_image_digest,
route_contract_version, database/schema identity, read_only caller/role,
exact taxon UUID, source external ID/accession/DOI where relevant,
request selector, HTTP status/content type or SQL result state,
schema_state, data_state, page limit/offset/complete total,
bounded record IDs and source URLs, unique/ambiguous/unlinked count,
response/build hash, writer side-effects checked, reviewer
```

Do not attach credentials, raw database URLs, unrestricted sequence payloads, or all-species dumps. Keep native API, Website BFF and browser receipts as separate evidence layers. A fixture test or a `200` response by itself is not production qualification.
