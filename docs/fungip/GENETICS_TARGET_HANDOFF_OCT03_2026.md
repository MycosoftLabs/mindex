# Explicit target binding for captured FungiP reference imports

Prepared 2026-10-03. This is a local source candidate for independent review. No database connection, import, API reload, AWS action or deployment was performed. The existing qualification data and earlier receipts are unchanged.

## Behavior and boundaries

The importer remains offline by default. Without new options, `--apply` still accepts only the existing private qualification target. A portable apply requires both `--target-manifest` and `--target-sha256`. The manifest's exact raw bytes are checked before a connection is attempted; its fixed schema permits no credentials, SQL, service names or arbitrary connection options.

An operator chooses and separately reviews the connection host/port/database and the finite set of expected server IP addresses, server port and database. These are declared expectations, not automatically discovered or independently proven AWS account/instance identity. The connected session must use `sslmode=verify-full`, report active TLS, and return an exact permitted address, port and database from the server identity query before source rows are read. PostgreSQL address output with `/32` or `/128` is compared as an IP address, not by text prefix. A mismatch aborts the transaction before any source or genetic-sequence insert.

The connection host must be a single literal IP or DNS hostname; host lists, Unix sockets, URL credentials, `hostaddr`, `service`, `options` and target-session overrides are not accepted in the DSN. Portable mode also rejects inherited `PGHOSTADDR`, `PGSERVICE`, `PGSERVICEFILE`, `PGOPTIONS`, `PGLOADBALANCEHOSTS` and `PGTARGETSESSIONATTRS`. An explicitly supplied `sslrootcert` path may be used for the approved trust root. Credentials stay in the separately supplied `FUNGIP_IMPORT_DSN`, never in the target JSON or receipt. Reports carry only target mode/hash and fixed failure codes, not DSN/driver error details.

The same pinned data policy applies to either target: catalog SHA `b33d08f061d8cbd1cfdda0ca0ace6b174ada2d3cfba84f3e04b1980f5bf82a9a`, preflight SHA `6f25ccf1757155ce891832b5e6ccde608ea95a01f51d613deb42370a409e13d8`, projection SHA `9d0a68c14ef3e79fb234b72a0fec8cbd4619d40e26db20af8dee853dcd571dc2`. All 300 full reference sequences remain; 246 require the exact existing canonical UUID/provider/name/rank/kingdom revalidation and 54 remain null. Only 42 have the previously established complete-ITS span. There is no name-only mapping or UUID replacement.

The destination must already contain the real schema, bound `fungip.species` records and matching canonical/provider identities. This option neither migrates nor invents them. Existing accession or alias conflicts reject the entire batch; no existing accession is overwritten. Exact semantic replay is unchanged. A failed commit acknowledgement remains `commit_outcome_unknown` and requires readback rather than an automatic retry. A fresh output path is mandatory; prior receipts are never overwritten.

## Operator input and command

The following is a non-operational documentation example only; the hostname and IPs are reserved examples, not a deployable configuration:

```json
{
  "schema": "fungip.import_target.v1",
  "connection": {"host": "database.example.invalid", "port": 5432, "dbname": "qualified_fixture"},
  "server": {"addresses": ["192.0.2.10"], "port": 5432, "dbname": "qualified_fixture"}
}
```

After the operator has selected and reviewed the real target document, provide its raw SHA-256 and a new receipt path. Set the four variables below to approved local paths/hash; do not place DSN credentials in the command or document. This command is offline unless `--apply` is deliberately appended:

```powershell
python -B -m mindex_etl.fungip.genetic_references "$env:FUNGIP_CAPTURED_PACKAGE" --preflight "$env:FUNGIP_GENETICS_PREFLIGHT" --output "$receiptFile" --target-manifest "$targetFile" --target-sha256 "$targetSha256"
```

An apply must be performed by the designated operator only after source review and qualification of the intended target. No such apply is authorized or demonstrated by this document. A successful offline projection with a target hash expressly reports `connected_target_verified=false`.

## Local validation

The original captured-reference tests were left unchanged. The combined focused run passed **76 tests in 7.18 seconds**, using the retained exact package/preflight and synthetic connection/transaction objects only. New tests cover target pairing/raw hash/strict schema, ambiguous or mismatched targets, TLS requirements, ambient overrides, server address/port/database mismatch, source-read prevention on mismatch, explicit CLI apply with a fake connection, receipt privacy, preserved nullable mappings, no-op replay, conflict rejection, late-insert rollback and unknown commit acknowledgement. No SQL was executed by a database.

Reproduce with an existing approved Python environment and the retained captured inputs explicitly configured:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m pytest -o addopts='' -p pytest_asyncio.plugin -p no:cacheprovider -q --tb=short tests/test_fungip_genetic_references.py tests/test_fungip_genetic_reference_targets.py
```

Unset captured inputs skip the associated real-data qualification cases; synthetic target controls still run. This does not qualify libpq TLS negotiation, DNS, native SQL transactions, a managed database proxy, actual server identity, AWS or production delivery. The shared publication manifest is intentionally not changed while another owner is editing disjoint Earth source; it must be refreshed after the combined source review.
