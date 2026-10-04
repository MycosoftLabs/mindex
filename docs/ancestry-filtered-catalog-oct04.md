# Ancestry native filtered catalog handoff

## Contract

The `GET /taxa` route filters, counts, sorts, and pages `core.taxon`. Ordinary browsing excludes merged rows; explicit `ids` lookups retain their existing direct-lookup behavior. `pagination.total` is the filtered count snapshot before `limit`/`offset`; it uses a TTL cache, so see the query freshness/consistency metadata before presenting it as current.

| Query parameter | Accepted values | Persisted evidence |
| --- | --- | --- |
| `family` | Exact displayed family value | One resolved value: nonblank `core.taxon.metadata.family`, else the first exact linked FungiP family ordered by `species_id`, else `Unknown`. Exact identity, accepted-name, species-rank, Fungi-kingdom and resolved-state checks apply. `family_evidence` preserves core first and FungiP second, including disagreement. |
| `category` | `all`, `edible`, `medicinal`, `poisonous`, `psychoactive`, `gourmet`, `unknown` | Explicit `metadata.edibility`, `metadata.characteristics`, and source-qualified `bio.taxon_trait` / `bio.taxon_characteristic` entries named `edibility` or `characteristic(s)`. Values use the Website alias set and underscore/hyphen/whitespace normalization; no name or description inference occurs |
| `filter` | `all`, `has_images`, `has_description` | Image URL selection from `metadata.default_photo.medium_url`, `metadata.default_photo.url`, `metadata.photos[0].url`, or exact validated FungiP image; description from nonblank `core.taxon.description` then `metadata.description` |
| `order_by` | `server`, `canonical_name`, `name-asc`, `name-desc`, `observations`, `observations_count`, `obs_count`, `family`, `featured` | Native name, stored observation count, persisted family, or the Website's derived featured rule (`obs_count > 5000`) |

`limit` and `offset` retain the existing pagination contract and server maximum; this work adds no 500-row cap. Observation sort is count descending, then the same validated image-presence tie-break, then canonical name. Name and featured sorts have deterministic canonical-name and UUID tie-breaks. `lineage_contains` retains PostgreSQL `ILIKE` pattern semantics; percent and underscore in user input remain wildcards.

The response adds `query.status`, `query.count_scope`, `query.count_consistency`, `query.count_cache_state`, `query.count_cache_ttl_seconds`, `query.filter_sources`, and `query.partial_reasons`. List totals use the existing 300-second count cache: `fresh_query` means the count was queried on this request, and `cache_hit` means it may reflect data from an earlier request. Both states declare `best_effort_not_atomic`; the page and count are not a database snapshot pair. Consumers must not label a cached total as an exact count of the currently returned rows. `empty` is emitted only for a fresh zero count with no returned rows; a cached zero paired with rows remains `available`. `partial` means an optional FungiP source table, identifier search, or category-evidence projection was unavailable. A database/query outage remains HTTP 503 and cannot be mistaken for an empty page. Invalid category or sort values return HTTP 422.

Family filtering, sorting and each returned `family` use the same resolved scalar. Exact linked FungiP disagreement is retained in bounded evidence rather than broadening family membership. Each row exposes at most two `family_evidence` entries (value ≤200 characters, species ID ≤64), in stable source order: `core.taxon.metadata.family`, then `fungip.species.record.taxonomy.family`. Category pages expose at most 64 evidence items (value ≤120), only from `core.taxon.metadata.edibility`, `core.taxon.metadata.characteristics`, `bio.taxon_trait`, and `bio.taxon_characteristic`; category values are the five recognized literals and no names/descriptions are inferred. Image selection returns one usable URL (≤2048) with its matching source, source URL, attribution (≤512), and license (≤128); source values are the exact field paths declared by `TaxonImageSelection`. Missing optional FungiP tables report partial core-only coverage. A source-dependent nonempty page whose required enrichment fails returns 503; a real zero-match query remains empty.

## Source limits and migration needs

There is no dedicated `core.taxon.family` column or persisted `featured` field in this source snapshot. Family therefore reads the stored root metadata value and, when present, an exact verified FungiP family field. Featured uses the Website's current derived observation threshold. No DDL change is needed for this contract; filters fail with 503 when their required core trait relations cannot be read. If a future consumer needs source-specific family provenance beyond the current core metadata value, the ingest contract must first persist that field's source alongside the value.

This is an endpoint contract only. It does not modify Website BFF or client paging/filter behavior. The Website owner must map its existing controls to these parameters in a separate change, inspect the v2 count freshness/consistency fields before presenting totals, and keep any remaining page-local FungiP-only records visibly scoped until that adoption is reviewed.

## Private PostgreSQL evidence

`tests/test_taxon_filtered_catalog_postgres.py` creates a finite synthetic fixture only when `MINDEX_FILTERED_TEST_DATABASE_URL` names a database beginning `mindex_filtered_fixture_` on loopback. It refuses any other database. The fixture mirrors the relevant core and FungiP uniqueness, foreign-key and hash/status checks, with synthetic exact, mismatched, ambiguous and conflicting identities. Checks cover 131 matching taxa across offset 120, resolved family plus disagreement evidence, category evidence from both trait relations, selected photo attribution and safe fallback, unsafe URL exclusion, observation sorting, and optional-table behavior. A separate control documents the existing count-cache behavior: after a new matching row is inserted inside the TTL, a subsequent page may have a cached total; v2 reports `cache_hit`, the TTL, and non-atomic consistency. Fixture rows are synthetic, not captured production inputs. Exact guarded SQL/runtime output is recorded in [ancestry-filtered-catalog-postgres-receipt-oct04.md](ancestry-filtered-catalog-postgres-receipt-oct04.md).

The test runner used a separate private PostgreSQL cluster on loopback port 55502 and the existing isolated Python environment with FastAPI 0.111.1 / Starlette 0.37.2. No database other than the explicitly named private fixture was contacted; no migration was applied to a shared database.

## Cursor staging, apply, and rollback

Use a fresh MINDEX worktree or an isolated Cursor checkout. Do not reuse the provider-version worktree with its unrelated `mindex_test*_utf8.txt` line-ending changes.

1. Confirm the repository is MINDEX and the starting commit is frozen candidate `01e0709cbf80f8f1e8414e7105d639def7eabee1` (`codex/research-identity-filtered-catalog-oct04`), whose base is `0cefc83e41df9db93dcf15a8d96fc11ea0cd7094`. Confirm no shared DB URL, deployment, or other repo is selected.
2. Review the manifest and `git diff --check`. Stage only the paths listed in the manifest. Do not stage runtime data, local environment files, or any unrelated EOL changes.
3. Run the focused offline route test and, only if a dedicated loopback database named `mindex_filtered_fixture_*` is available, the guarded PostgreSQL test. Do not point it at VM189, staging, production, or a shared dev database.
4. Apply the source change by cherry-picking the manifest's successor candidate commit into the intended MINDEX branch after review. There is no schema migration to apply.
5. Roll back a published integration with `git revert <successor_candidate_commit>` on that integration branch. For an unshared local candidate, switch away and retain the branch for review; do not hard-reset a checkout that may contain other work.

## Integration manifest

See [ancestry-filtered-catalog-integration-manifest-oct04.json](ancestry-filtered-catalog-integration-manifest-oct04.json) for exact pins, files, test commands, and boundaries.
