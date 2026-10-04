# Private filtered-catalog PostgreSQL receipt

This receipt records an isolated guarded integration run only. The fixture contains synthetic rows, not captured MINDEX production data.

The test guard verifies both the database name prefix and loopback server address before any fixture DDL or reset:

```sql
SELECT current_database() AS database_name, host(inet_server_addr()) AS server_address;
```

Observed test target: database `mindex_filtered_fixture_correctness_oct04`, `127.0.0.1:55502`, PostgreSQL 17.11. The private cluster was started under a separate data directory for this run. It was stopped after validation; the private data directory was retained. No connection was made to VM189, staging, production, or a shared development database.

The fixture mirrors the tested source constraints: `core.taxon_external_id(source, external_id)` unique with its canonical-taxon foreign key; `fungip.species(taxon_id)` unique and foreign-keyed to core; constrained FungiP IDs, resolution states, catalog and record SHA-256 fields; and the page-verification/token-attempt foreign-key relationships. It seeds 130 synthetic core taxa with an explicit edible trait, exact and mismatched identity links, a two-link ambiguous identity, a core/FungiP family disagreement, safe/unsafe photos, source-qualified trait and characteristic evidence, and observation rows.

The guarded suite exercised:

- 131 exact family/category matches across `limit=120&offset=120`, with 11 rows on the final page and trait plus characteristic evidence on the linked row.
- Exact validated FungiP family/photo eligibility; mismatched and ambiguous identity rejection; core-first family resolution with the disagreement retained as evidence; and family sorting against the same displayed family.
- Safe photo fallback and matching attribution/license, plus rejection of a backslash URL.
- Stored-observation sorting and optional FungiP table absence with partial status.
- Count cache behavior: a first filtered query returned total 131; after inserting a new matching row and committing, the next page still had two rows but reported cached total 131 and `query.count_cache_state=cache_hit`. The response declares `query.count_consistency=best_effort_not_atomic` and `query.count_cache_ttl_seconds=300`; the contract explicitly forbids presenting this cache hit as an exact current page count.

The frozen-predecessor audit recorded eight test groups, 34 actual route calls and 27 observations (20 passes, 7 negatives). Five negative observations reproduced the assigned correctness gaps: two family identity/projection cases, one missing category evidence projection, one enrichment-failure state, and one unsafe backslash photo. The other two were retained as inherited behavior and are explicitly qualified above: `lineage_contains` preserves PostgreSQL wildcard semantics, and count-cache hits may lag rows in a later page. These were bounded synthetic-fixture observations, not live-189 or production findings.

Guarded command:

```powershell
$env:MINDEX_FILTERED_TEST_DATABASE_URL = 'postgresql+asyncpg://postgres@127.0.0.1:55502/mindex_filtered_fixture_correctness_oct04'
& '<existing-isolated-venv>\Scripts\python.exe' -m pytest -o addopts='' -q --tb=short tests/test_taxon_filtered_catalog_postgres.py
```

The whole focused set, including this guarded suite, passed **75 tests**. The full-set command and exact test count are in the integration manifest. A separate frozen-source route audit reported that PostgreSQL `ILIKE` wildcard behavior for `lineage_contains` is inherited behavior; this successor preserves it and does not claim literal wildcard escaping.
