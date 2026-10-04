# Private filtered-catalog PostgreSQL receipt

This receipt records the guarded integration run only. It contains synthetic rows, not captured MINDEX production data.

Guard enforced by `tests/test_taxon_filtered_catalog_postgres.py` before schema setup:

```sql
SELECT current_database() AS database_name, host(inet_server_addr()) AS server_address;
```

Observed guard result: database `mindex_filtered_fixture_oct04c`, address `127.0.0.1`. The test refuses any database outside the `mindex_filtered_fixture_*` naming scope or any non-loopback server address.

Runtime and constraint receipt:

```text
PostgreSQL 17.11 on x86_64-windows, compiled by msvc-19.44.35228, 64-bit
core.taxon rows: 134
bio.taxon_trait rows: 131
fungip.species rows: 4
UNIQUE constraints: core.taxon_external_id(source, external_id); fungip.species(taxon_id)
```

The test fixture preserves both production uniqueness constraints. It demonstrates an exact linked row, a mismatched source identifier, and an ambiguous record with two distinct source-qualified identifiers that resolve to two canonical taxa. The latter two are excluded from family and image matches. All rows are finite synthetic taxa, traits, and source records.

Guarded command and result:

```powershell
$env:MINDEX_FILTERED_TEST_DATABASE_URL = 'postgresql+asyncpg://postgres@127.0.0.1:55499/mindex_filtered_fixture_oct04c'
.\.venv-filtered-catalog\Scripts\python.exe -m pytest -q tests/test_taxon_filtered_catalog_postgres.py
```

Result: **4 passed**. The matching `family=Agaricaceae&category=edible` page has total `131`; at `limit=120&offset=120` it returns `11` rows, including the one exact linked FungiP row. The `family=Agaricaceae&filter=has_images` page returns exactly the valid linked row (total `1`), excluding both conflicting identities. Descending observation sort places the fixture with two stored `obs.observation` rows first. A separate guarded run drops only the optional `fungip.species` table in this private fixture and confirms family sort still uses core metadata while reporting `partial`. No schema migration was applied to this cluster or any shared database.
