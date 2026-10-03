# FungiP and species integration: source handoff

Prepared 2026-10-03. This branch is a source integration for operator review, not a deployment or production qualification.

## Source relationship

- Public base: `42b876fcfca2e86b0365e8fe8afab628d6a94705`.
- Local feature checkpoint: `a54b2a4ad1b5286d5014987a6fd3993a9e0dd3d0`.
- Feature comparison base: `687d3d9074e1af6a17c92b9c6b4b6887373b0c00`.
- Selected source, migrations and tests were applied as an aggregate three-way diff. The original local commit history and internal artifacts are not parents of this publication branch.
- Current main workflows, legal policy, bounding-box fixes, Worldview fixes and other unrelated changes are preserved.

## What the code provides

The FungiP catalog, First40 source association and public index modules retain source identities and provenance checks without inventing canonical UUIDs, location, media or completed launches. API data comes from stored database rows. Runtime modules do not load internal audit files from this document directory.

Ancestry detail can return stored genetics with an exact canonical taxon filter and observation reads without completion events. Earth observation projection preserves canonical versus provider identity, source media rights and unknown fields. Explicit `read_only` options suppress the documented writes/events; they do not imply that every GET or configured provider is universally side-effect-free.

Biological Search uses the declared canonical, observation, compound and sequence tables. Database operations are sequential on the request session. Failed transactions recover before reuse; a recovery failure stops further queries.

The captured-reference importer keeps the full captured DNA reference and its original metadata. It does not claim that a reference is an entire genome or a complete ITS span. It accepts only the pinned qualification inputs and its explicit isolated local target. It is not a general production importer and was not run against a database during this integration.

## Deliberate differences from the local checkpoint

The only application-source conflict was Unified Search. Current main's option-bound cache keys and explicit HTTP 503 partial/unavailable failures are retained. A failed selected domain is not converted to a successful empty result, and no fallback persistence, cache write or completion event follows that incomplete selected-domain response. Healthy surviving rows remain in the sanitized partial-response body. FungiP retains its separate optional-index availability contract.

Main's savepoint query interface is composed with the candidate's sequential recovery, cancellation and awaited storage behavior. Query logs retain the domain and exception class, not raw database exception text. The related test doubles now expose savepoint/cache-option interfaces; two previous successful-partial candidate assertions now require main's HTTP 503 envelope. Existing main assertions for privacy, cache separation, surviving UUID/date serialization and no effects on failed domains remain intact.

Captured-data tests now require explicit inputs; no personal workstation default or silently substituted dataset remains. Rejected non-loopback address fixtures use documentation-reserved addresses. Other selected feature source is carried unchanged except the main-compatible three-way integration in the API registration and observation route.

## Inputs intentionally not published

The local 300-record audit, launch drafts, per-species gaps, First40 snapshot/report/disagreements, source manifests, validation receipts and raw logs remain outside this branch. These are data/provenance artifacts, not runtime module dependencies. This omission does not mean their underlying public scientific or token fields are confidential; a deliberately selected public data release is separate from exposing internal custody and operator metadata.

The historical `fungip_validate.py`, `fungip_validate_successor.py` and `fungip_source_manifest.py` task scripts are not included. Their sibling checkout assumptions, source pins and old hard-coded result counts are not portable operator commands. Their helper-only manifest test is also omitted. The feature's synthetic unit and protocol tests remain included.

No database dump, image/archive binary, local environment file, credentials, generated dependency tree or compiled application output is added. Four pre-existing normalized text outputs visible in this checkout are not part of the commit.

## Portable qualification

Install the repository's normal test dependencies through the operator's approved environment. This work installed nothing. Use the existing Python interpreter and disable unrelated pytest plugin autoload and cache/bytecode writes when reproducing the bounded offline checks.

The following variables point to separately retained local inputs. Values are not recorded in this handoff:

| Variable | Required input |
|---|---|
| `FUNGIP_CAPTURED_AUDIT` | Exact captured package-audit JSON used by catalog/accepted-usage/projection checks |
| `FUNGIP_FIRST40_SNAPSHOT` | Exact accepted parent-bound First40 snapshot JSON |
| `FUNGIP_CAPTURED_PACKAGE` | Recovered 300-record package directory, including DNA sidecars |
| `FUNGIP_GENETICS_PREFLIGHT` | Exact accepted genetics projection preflight JSON |

An unset input causes an explicit captured-data skip. A configured missing, malformed or hash-mismatched input fails; it is not reclassified as a skip. Existing record, catalog, snapshot, sequence and projection digest assertions remain. Supplying these paths neither connects to a database nor authorizes an apply.

Relevant input users are `test_fungip_catalog.py`, `test_fungip_accepted_usage.py`, `test_fungip_real_snapshot_projection.py` and `test_fungip_genetic_references.py`. The ordinary API and ETL entrypoints accept their data through their documented row or CLI arguments; removing the internal document artifacts does not break application imports.

### Reproduce the selected offline suites

From the repository root, in PowerShell with the repository's test dependencies already available:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$featureTests = @(git diff --name-only --diff-filter=A 42b876fcfca2e86b0365e8fe8afab628d6a94705 b78e1fb59b21fd2b02c808e32dc68dfaf4be4150 -- tests | Where-Object { $_ -match '^tests/test_.*\.py$' })
if ($LASTEXITCODE -ne 0 -or $featureTests.Count -ne 20) { throw 'Expected the 20 pinned feature test files' }
python -B -m pytest -o addopts='' -p pytest_asyncio.plugin -p no:cacheprovider -q --tb=short @featureTests tests/test_batch5_search_contract.py tests/test_batch9_bbox_worldview_contract.py tests/test_worldview_search_contract.py
if ($LASTEXITCODE -ne 0) { throw 'Selected offline qualification failed' }
```

With the four input variables unset, captured-data cases skip explicitly. To reproduce the captured-data run, the operator must first set each variable in the table to its separately retained exact input; do not substitute generated data. The command does not apply migrations or import into a database. A passing run does not establish database, provider, authenticated browser or deployment acceptance.

The final affected/default-input check used the same environment and these explicit suites:

```powershell
python -B -m pytest -o addopts='' -p pytest_asyncio.plugin -p no:cacheprovider -q --tb=short tests/test_fungip_catalog.py tests/test_fungip_accepted_usage.py tests/test_fungip_real_snapshot_projection.py tests/test_fungip_genetic_references.py tests/test_unified_search_session_contract.py tests/test_unified_search_fungip_index.py tests/test_batch5_search_contract.py tests/test_unified_search_biological_schema.py tests/test_earth_search_read_only.py tests/test_unified_search_read_only.py
if ($LASTEXITCODE -ne 0) { throw 'Affected offline qualification failed' }
```

[The source manifest](PUBLIC_SOURCE_MANIFEST_OCT03_2026.json) binds the 49 selected postimage files by repository-relative path, raw byte size and SHA-256. It excludes itself to avoid recursive hashing. Its source checkpoint is the code/test commit; this handoff and manifest are a subsequent documentation-only change. Line-ending conversion in a different checkout can change raw-byte hashes without changing Git's normalized text blob.

## Verification performed

- 378 selected offline cases passed with the four named, privately supplied captured inputs. The run included all 20 newly added feature test files plus current-main Search, bounding-box/Worldview and Worldview Search contract suites. It exercised actual source code against synthetic sessions/transports and retained source data; it did not execute SQL against a server.
- After the encoding-only correction and removal of a redundant cancellation recovery branch, the affected portable Search/Earth/cache checks and optional-data subset passed: 79 passed, 92 explicitly skipped with no captured inputs configured. The 72 Search/Earth/current-main checks within that subset passed.
- Earlier integration failures exposed missing savepoint/cache methods in predecessor test doubles and an absent standalone FungiP dispatch stub. Those were reconciled to the merged contracts before the passing runs. No production failure was inferred from those fixtures.
- Existing Pydantic and datetime deprecation warnings remain. This is not a full repository, application build, provider, authenticated browser or native database qualification.

Operator review must confirm schema/migrations against the intended existing database, use the separately retained data and receipts, and qualify the exact integrated revision. No migrations, catalog imports, genetics imports, API reloads, provider calls, publication, deployment or rollback were performed by this preparation. Production integration remains with the designated operator.
