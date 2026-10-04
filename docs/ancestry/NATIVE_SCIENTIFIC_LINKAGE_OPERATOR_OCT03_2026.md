# Native scientific linkage operator notes — 2026-10-03

This is a portable review packet for the MINDEX owner and Cursor. It has not been executed. It grants no database, schema, migration, ETL, AWS, production, or deployment authorization.

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

After the schema receipt, run separately bounded queries in independent read-only transactions. `$1` is the exact splitgill UUID `6db28640-67fb-4808-90de-956a856366f7`; no name lookup substitutes for it.

```sql
-- Exact source crosswalk only. A result must be inspected for uniqueness.
SELECT source, external_id, taxon_id, created_at
FROM core.taxon_external_id
WHERE source = 'ncbi' AND external_id = $1
ORDER BY taxon_id
LIMIT 3;

-- Stored sequences and linkage state; never export sequence bodies for this check.
SELECT id, accession, version, taxon_id, source, source_url, gene,
       region, sequence_type, sequence_length,
       metadata->'source_taxon_ids' AS source_taxon_ids,
       metadata->'taxon_linkage' AS taxon_linkage
FROM bio.genetic_sequence
WHERE taxon_id = $1::uuid
ORDER BY accession
LIMIT 8;

-- Stored assemblies are separate from deposited nucleotide sequences.
SELECT id, taxon_id, source, accession, assembly_level, release_date
FROM bio.genome
WHERE taxon_id = $1::uuid
ORDER BY release_date DESC NULLS LAST, accession
LIMIT 8;

-- Exact species-compound assertions with evidence and citations.
SELECT tc.taxon_id, tc.compound_id, c.name, c.pubchem_id,
       tc.relationship_type, tc.evidence_level, tc.tissue_location,
       tc.source, tc.source_url, tc.doi
FROM bio.taxon_compound tc
JOIN bio.compound c ON c.id = tc.compound_id
WHERE tc.taxon_id = $1::uuid
ORDER BY tc.compound_id
LIMIT 8;

-- Exact publication links. Current link schema does not record its evidence source.
SELECT pt.publication_id, pt.taxon_id, pt.relevance_score,
       p.doi, p.source, p.url
FROM bio.publication_taxon pt
JOIN core.publications p ON p.id = pt.publication_id
WHERE pt.taxon_id = $1::uuid
ORDER BY pt.relevance_score DESC NULLS LAST, pt.publication_id
LIMIT 8;

-- Both directions are relevant; keep source and target intact.
SELECT id, source_taxon_id, target_taxon_id, interaction_type,
       evidence_source, evidence_url
FROM bio.taxon_interaction
WHERE source_taxon_id = $1::uuid OR target_taxon_id = $1::uuid
ORDER BY id
LIMIT 8;
```

For the source-ID crosswalk query, record the exact returned row count and UUIDs. Do not call the pair linked unless there is exactly one taxon UUID and its name/rank is consistent with the authoritative source record. Zero means no stored crosswalk was found under that exact key; two or more is ambiguous. Do not resolve either state by name, genus, fuzzy similarity, first row, or provider search result.

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

## 3. Publication-link schema gap and proposal

Current `bio.publication_taxon` contains only publication ID, taxon UUID, relevance score and creation time. The current reader can return publication-level DOI/source/URL, but that does not document how the taxon-to-paper association was established. Current inspected publication ETL writes `core.publications`; no exact taxon-link write path was found in the bounded source search.

Before any publication-link backfill, the MINDEX owner should propose a reviewed additive schema change for association-level provenance (source system, source record/identifier, source URL, association method, recorded timestamp, and evidence status), validate compatibility with both the full and minimal bootstrap schema, and supply a guarded rollback plan. The schema change and link creation must be separate reviewed steps. A candidate link must cite the exact publication ID/DOI and an authoritative source record or curator assertion for the species; title/name search overlap alone is not sufficient. No such DDL or link backfill is included or applied by this candidate.

One additive shape for review (not an approved migration and not executed) is:

```sql
ALTER TABLE bio.publication_taxon
  ADD COLUMN association_source TEXT,
  ADD COLUMN association_source_record_id TEXT,
  ADD COLUMN association_source_url TEXT,
  ADD COLUMN association_method TEXT,
  ADD COLUMN association_state TEXT NOT NULL DEFAULT 'legacy_unverified',
  ADD COLUMN association_recorded_at TIMESTAMPTZ;
```

Existing links would remain explicitly `legacy_unverified`; no historical link should be upgraded merely because the new columns exist. A future apply should first inspect the complete table/schema and backup policy, apply additive DDL in a reviewed migration, verify the columns and defaults, and only then run a separately staged finite association backfill. A rollback should be permitted only before any reviewed association evidence has been recorded; it must stop if any proposed provenance column contains non-null evidence or any state differs from `legacy_unverified`, then drop only the newly introduced empty columns in a transaction. Once provenance rows are written, rollback requires an owner-approved data-preservation plan and cannot be a blind column drop.

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
