# Ancestry native filtered catalog handoff

## Contract

The `GET /taxa` route filters, counts, sorts, and pages `core.taxon`. Ordinary browsing excludes merged rows; explicit `ids` lookups retain their existing direct-lookup behavior. `pagination.total` is the count after every supported query filter and before `limit`/`offset`; it is no longer an unfiltered total paired with page-local filters.

| Query parameter | Accepted values | Persisted evidence |
| --- | --- | --- |
| `family` | Exact displayed family value | `core.taxon.metadata.family`; for an exact linked FungiP member, `fungip.species.record.taxonomy.family` after the same unique external-ID, accepted-name, rank, kingdom, and resolution checks as the public projection |
| `category` | `all`, `edible`, `medicinal`, `poisonous`, `psychoactive`, `gourmet`, `unknown` | Explicit `metadata.edibility`, `metadata.characteristics`, and source-qualified `bio.taxon_trait` / `bio.taxon_characteristic` entries named `edibility` or `characteristic(s)`. Values use the Website alias set and underscore/hyphen/whitespace normalization; no name or description inference occurs |
| `filter` | `all`, `has_images`, `has_description` | Image URL selection from `metadata.default_photo.medium_url`, `metadata.default_photo.url`, `metadata.photos[0].url`, or exact validated FungiP image; description from nonblank `core.taxon.description` then `metadata.description` |
| `order_by` | `server`, `canonical_name`, `name-asc`, `name-desc`, `observations`, `observations_count`, `obs_count`, `family`, `featured` | Native name, stored observation count, persisted family, or the Website's derived featured rule (`obs_count > 5000`) |

`limit` and `offset` retain the existing pagination contract and server maximum; this work adds no 500-row cap. Observation sort is count descending, then the same validated image-presence tie-break, then canonical name. Name and featured sorts have deterministic canonical-name and UUID tie-breaks.

The response adds `query.status`, `query.count_scope`, `query.filter_sources`, and `query.partial_reasons`. `available` means the requested source fields were queried; `empty` means a complete query returned zero matches; `partial` means an optional FungiP source table or identifier search was unavailable and results use the stored core fields only. A database/query outage remains HTTP 503 and cannot be mistaken for an empty page. Invalid category or sort values return HTTP 422.

## Source limits and migration needs

There is no dedicated `core.taxon.family` column or persisted `featured` field in this source snapshot. Family therefore reads the actually stored root metadata value and, when present, a currently verified FungiP family field. Featured uses the Website's current derived observation threshold. No DDL change is needed for this contract; filters fail with 503 when their required core trait relations cannot be read. If a future consumer needs source-specific family provenance beyond the current core metadata value, the ingest contract must first persist that field's source alongside the value.

This is an endpoint contract only. It does not modify Website BFF or client paging/filter behavior. The Website owner must map its existing controls to these parameters in a separate change and keep any remaining page-local FungiP-only records visibly scoped until that adoption is reviewed.

## Private PostgreSQL evidence

`tests/test_taxon_filtered_catalog_postgres.py` creates a finite synthetic fixture only when `MINDEX_FILTERED_TEST_DATABASE_URL` names a database beginning `mindex_filtered_fixture_` on loopback. It refuses any other database. The fixture mirrors `core.taxon_external_id(source, external_id)` and `fungip.species(taxon_id)` uniqueness, with 130 synthetic matching taxa, an exact FungiP crosswalk, a mismatched identifier, and an ambiguous record containing two distinct source IDs mapped to two canonical rows. Checks cover an exact matching count of 131 across offset 120, validated family/photo evidence, exclusion of conflicting/ambiguous source identities, and observation sorting. Fixture rows are synthetic and are not captured production inputs. Exact guarded SQL/runtime output is recorded in [ancestry-filtered-catalog-postgres-receipt-oct04.md](ancestry-filtered-catalog-postgres-receipt-oct04.md).

The test runner used a private PostgreSQL cluster on loopback port 55499 and an isolated worktree-local Python environment with FastAPI 0.111.1 / Starlette 0.37.2. No database other than the explicitly named private fixture was contacted; no migration was applied to a shared database.

## Cursor staging, apply, and rollback

Use a fresh MINDEX worktree or an isolated Cursor checkout. Do not reuse the provider-version worktree with its unrelated `mindex_test*_utf8.txt` line-ending changes.

1. Confirm the repository is MINDEX and the starting commit is `0cefc83e41df9db93dcf15a8d96fc11ea0cd7094` (`codex/research-identity-provider-versions-oct04`). Confirm no shared DB URL, deployment, or other repo is selected.
2. Review the manifest and `git diff --check`. Stage only the paths listed in the manifest. Do not stage runtime data, local environment files, or any unrelated EOL changes.
3. Run the focused offline route test and, only if a dedicated loopback database named `mindex_filtered_fixture_*` is available, the guarded PostgreSQL test. Do not point it at VM189, staging, production, or a shared dev database.
4. Apply the source change by cherry-picking the manifest's `candidate_commit` into the intended MINDEX successor branch, or use the current branch directly after review. There is no schema migration to apply.
5. Roll back a published integration with `git revert <candidate_commit>` on that integration branch. For an unshared local candidate, switch away and retain the branch for review; do not hard-reset a checkout that may contain other work.

## Integration manifest

See [ancestry-filtered-catalog-integration-manifest-oct04.json](ancestry-filtered-catalog-integration-manifest-oct04.json) for exact pins, files, test commands, and boundaries.
