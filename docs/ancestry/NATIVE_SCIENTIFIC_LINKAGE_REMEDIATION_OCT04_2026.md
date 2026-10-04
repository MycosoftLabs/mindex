# Native scientific linkage remediation — 2026-10-04

## Source pin and scope

This review candidate is based on frozen source commit `9e902613d192f4aeee7f60dd30d31f558d3221ed` and adds the independent-review fixes in commit `00f6d78ce5a7df456946ef1444ac48c720c70450`, branch `codex/ancestry-scientific-evidence-oct04`. The complete review branch is a clean diff from `origin/main` at `8da30115ca3aac6b67683138e02376bcde504a8f`.

The publication-linkage remediation changes the publication evidence importer, its additive evidence migration and focused regression tests. Later native API corrections change `mindex_api/routers/phylogeny.py`, `mindex_api/routers/taxon.py`, `mindex_api/services/ancestry_public_members.py`, focused regressions and a test-only FastAPI/Starlette compatibility runner. Neither remediation alters `bio.publication_taxon`, creates reviewed species-publication links, fetches providers, touches Cursor-owned all-species loaders, changes Website code, or applies schema/data changes outside isolated qualification databases.

## Behavior corrected

- Validate accession.version and source-content SHA-256 before interpreting a GenBank response, including records with no citable references. Wrong identity pins return without database calls.
- Distinguish newly inserted evidence from an idempotent existing row. Read back and return the persisted evidence UUID and candidate/accepted/rejected review state; a missing readback fails so the caller can roll back.
- Preserve mixed per-row review dispositions in the receipt. The importer never promotes or overwrites an existing disposition and does not create a `bio.publication_taxon` link.
- Require a nonblank reviewer for terminal dispositions on both INSERT and UPDATE. Include `evidence_id` in the immutable source-identity guard.

## Native lineage projection correction

The previous `/api/mindex/phylogeny` projector zipped `core.taxon.lineage` and `lineage_ids` by position, then assigned the selected row's rank to the last lineage name. A retained safe-flow capture showed the resulting cross-kingdom identity error: the selected `Bucephala albeola` species UUID (`e8c03e91-444e-48ed-9d7d-6d44c5486c0b`) appeared on the `Animalia` root and `Bucephala` was labeled `species`. The exact API response is retained at `outputs/ancestry-continuation-oct04/independent-flow-review/review-20261004T023940Z/ordinary_lineage.raw.json` (SHA-256 `35439cfb5e249f58ce91a7f897642788bb32b698c2811280a525fe8010e89a7e`).

The corrected projector checks each positional ancestor UUID against its own `core.taxon` row, including canonical name and compatible kingdom, before attaching that identity or its rank. Unverified names remain name-only with `unknown` rank. Misaligned arrays retain their raw values, mark the lineage `partial`, and do not transfer links across positions. The exact selected row is appended as the selected tip with its UUID, canonical name and rank; an inclusive final self-name is replaced by that verified tip. The response adds provenance and issue details while preserving the existing nested `tree` shape and top-level selected taxon fields.

An inclusive terminal self-link is exempt from ancestor mismatch reporting only when its final lineage name and UUID both match the exact selected row. Raw arrays remain in the response. A matching final name with a missing, malformed or conflicting UUID remains partial and retains its issue.

The retained Fungi search capture supplies the selected `Schizophyllum commune` identity (`6db28640-67fb-4808-90de-956a856366f7`, kingdom `Fungi`, rank `species`) at `outputs/ancestry-continuation-oct04/independent-flow-review/review-20261004T023940Z/schizophyllum_search.raw.json` (SHA-256 `ef76f98bc4ed6dc2e65da2fb7a92694e28c21c8c9058ba149bfd6354f98b99f2`). That capture does not include a native lineage response; the regression uses its exact selected identity with local fixture lineage arrays and does not claim the fixture arrays reflect production state.

## FungiP identifier search correction

The dedicated `/api/mindex/taxa/collections/fungip` route already searches collection fields, but the Website-facing ordinary `/api/mindex/taxa?q=...` route filtered `q` only against core canonical/common names. That discarded a linked FungiP row before count/page and before post-page member enrichment. The ordinary route now resolves FungiP ID, ticker and DNA accession matches to core UUIDs before its existing core count and page query. Resolution uses the exact unique `core.taxon_external_id` crosswalk and rechecks the stored UUID, accepted name, kingdom and species rank; unresolved/conflicting source rows do not add taxa. Optional FungiP index errors remain nonfatal, and normal all-kingdom paging and the separate First40 launch association path are unchanged.

The captured FungiP result for `Schizophyllum commune` includes FG032, ticker `SPLIT`, accession `PZ955173.1` and the linked UUID above. No First40 launch values were used to make these matches.

Identifier lookup and later page enrichment report separate optional-read states. A lookup `error/query_failed` now takes precedence over a later empty-page enrichment result of `available`; `source_table_missing` remains `unavailable`. Ordinary canonical/common-name results still return if the optional identifier lookup fails.

## Stored genetic accession lookup correction

`GET /api/mindex/genetics/accession/{accession}` now accepts either a base accession or an exact accession.version. A versioned request binds the stored base accession and the complete requested version separately, so a wrong version returns `404` without provider access. A base-only request retains the prior accession match behavior. The SELECT still returns the stored `version` and metadata-derived linkage provenance; provider fetch and storage remain exclusive to the explicit POST ingest endpoint.

## Validation executed

Project environment: Python 3.12.10; imports passed for psycopg 3.1.20, SQLAlchemy 2.0.49 and FastAPI 0.111.1.

- Focused project pytest: 36 passed across `test_publication_taxon_evidence.py`, `test_publication_evidence_insert_guard_sql_contract.py`, `test_genbank_taxon_linkage.py`, `test_wikipedia_source_provenance.py` and `test_wikimedia_commons_provenance.py`.
- Independent importer regression: 14/14 passed.
- Independent SQL guard-contract regression: 4/4 passed, including six faulty guard mutations rejected.
- Qualification-plan checks: 4/4 passed.
- Follow-up focused regressions passed through the test-only runner: `python tests/run_fastapi_starlette_compat_pytest.py tests/test_phylogeny_identity_projection.py tests/test_taxon_fungip_identifier_search.py tests/test_genetic_accession_version_lookup.py -q` completed 18 tests. Coverage includes exact inclusive self-link, conflicting self-link, failed identifier lookup with successful empty enrichment, nonfatal ordinary-name results, unavailable-source distinction, exact/wrong/versionless stored accession lookups, and the earlier Animalia/Fungi and ID/ticker/accession cases.
- The broader affected offline group also passed through that runner with `test_api_routes.py` excluded; First40 cases requiring absent local input bundles remain skipped. `test_api_routes.py` cannot be qualified through this installed app stack because of a separate Starlette/FastAPI runtime mismatch described below.
- Isolated API/DB qualification: the actual `/api/mindex/phylogeny` route returned HTTP 200 against PostgreSQL 17.11 on a task-owned loopback cluster at port 15911. The local `core.taxon` fixture used the exact selected Animalia and Fungi IDs/names/ranks from the retained captures, verified the Animalia UUID was not attached to the `Animalia` root, and verified both exact selected taxa were returned as tips. The Fungi lineage array was a local fixture because no native Fungi lineage capture was retained. Result SHA-256 `70c1a21b56d33ab203086006ae4166c59460bfd6f6010fcbce6eb5d887317c7d`; output and PostgreSQL logs are under `outputs/phylogeny-lineage-projection-oct04/`. The isolated server has been stopped.
- Isolated FungiP search/API qualification: the actual `/api/mindex/taxa?q=...` route returned the exact linked FG032 taxon with total `1` for each of `FG032`, `SPLIT` and `PZ955173.1`. The task-owned PostgreSQL fixture used the exact selected UUID and captured identity/accession/ticker, with a locally seeded matching external-ID crosswalk. Result SHA-256 `e6c2775065950a26c75200bad1d32f2c81168b73f3d5809a4c482a32bb2ad7a5`; same isolated database and stopped server as above.
- Exact PostgreSQL qualification: PASS on PostgreSQL 17.11 in a task-owned loopback-only cluster. Database `ancestry_pub_evidence_review_a4f1c842594f4f71a59167ee09b320a6`; port 15910. Migration SHA-256 `fc3e9389371c199b06baa1f5bb1ee0af2a4328a70ada08284292cc0311af1c15`. The pinned script verified reviewer guards, candidate-to-terminal updates, immutable source/evidence identities and rollback; it returned `PASS: exact migration guards, reviewed dispositions, immutable identity and rollback verified`. Its transaction rolled back, leaving no `core` or `bio` fixture schema. The task-owned server is stopped.
- Qualification log SHA-256: `440d2d8fe6672f515eBAA10ABA22EDC9F421D26AD0973E056557BB7B31E3B547`.

### Test import/runtime compatibility

No package was installed, upgraded or downgraded for this follow-up. The Owner1 interpreter is `C:\Users\Owner1\AppData\Local\Programs\Python\Python312\python.exe`: Python 3.12.10, pytest 8.4.2, FastAPI 0.111.1 and Starlette 1.7.0. Plain `python -m pytest ...` currently fails during collection because FastAPI 0.111.1 passes legacy `on_startup` and `on_shutdown` constructor keywords removed by Starlette 1.7.0 (`Router.__init__() got an unexpected keyword argument 'on_startup'`).

The checked-in `tests/run_fastapi_starlette_compat_pytest.py` is a test-only runner, not a service shim. It bridges Starlette `Router` construction only for empty legacy lifecycle handler lists; any nonempty startup/shutdown handlers fail immediately. The focused regressions then import and execute the real route/helper function bodies with fake SQL result mappings. They do not claim ASGI lifecycle, authentication or database qualification. Broader `TestClient` tests expose an additional mismatch (`FastAPI` expects Starlette's `max_body_size`), so those tests are excluded from the passing claim. Use the exact command above to reproduce the 18 focused tests.

The retained PostgreSQL API receipts earlier in this document were produced against the pre-follow-up `d17b22c` source and have not been rerun against these edge-case corrections or the accession-version correction. No new database action was performed for this follow-up, as requested.

The test cluster files and logs are in `outputs/ancestry-native-scientific-evidence-oct04/`. The PostgreSQL qualification script was not modified; no production or shared database was contacted.

## Remaining gates and rollback

This evidence qualifies importer and migration behavior in the bounded disposable PostgreSQL test only. It does not establish a live MINDEX taxon crosswalk, current provider availability, rights review, reviewed publication/taxon association, Website wiring, production migration, or deployment.

No production migration has been applied. If a later owner-approved staging apply fails before commit, roll back the transaction. If an applied environment requires rollback before any evidence is stored, first verify the evidence-table count is zero and use an owner-reviewed transaction to drop only the new trigger, functions and empty evidence table. Once evidence exists, preserve those records and use a forward repair; do not drop or delete provenance.

The four pre-existing `mindex_test*_utf8.txt` line-ending changes remain unstaged and are not part of this candidate.
