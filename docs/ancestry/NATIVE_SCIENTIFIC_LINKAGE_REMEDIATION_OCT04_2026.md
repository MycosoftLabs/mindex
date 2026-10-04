# Native scientific linkage remediation — 2026-10-04

## Source pin and scope

This review candidate is based on frozen source commit `9e902613d192f4aeee7f60dd30d31f558d3221ed` and adds the independent-review fixes in commit `00f6d78ce5a7df456946ef1444ac48c720c70450`, branch `codex/ancestry-scientific-evidence-oct04`. The complete review branch is a clean diff from `origin/main` at `8da30115ca3aac6b67683138e02376bcde504a8f`.

The publication-linkage remediation changes the publication evidence importer, its additive evidence migration and focused regression tests. A later, separate native API correction in this branch changes only `mindex_api/routers/phylogeny.py` and its focused regression tests. Neither change alters `bio.publication_taxon`, creates reviewed species-publication links, fetches providers, touches Cursor-owned all-species loaders, changes Website code, or applies schema/data changes outside isolated qualification databases.

## Behavior corrected

- Validate accession.version and source-content SHA-256 before interpreting a GenBank response, including records with no citable references. Wrong identity pins return without database calls.
- Distinguish newly inserted evidence from an idempotent existing row. Read back and return the persisted evidence UUID and candidate/accepted/rejected review state; a missing readback fails so the caller can roll back.
- Preserve mixed per-row review dispositions in the receipt. The importer never promotes or overwrites an existing disposition and does not create a `bio.publication_taxon` link.
- Require a nonblank reviewer for terminal dispositions on both INSERT and UPDATE. Include `evidence_id` in the immutable source-identity guard.

## Native lineage projection correction

The previous `/api/mindex/phylogeny` projector zipped `core.taxon.lineage` and `lineage_ids` by position, then assigned the selected row's rank to the last lineage name. A retained safe-flow capture showed the resulting cross-kingdom identity error: the selected `Bucephala albeola` species UUID (`e8c03e91-444e-48ed-9d7d-6d44c5486c0b`) appeared on the `Animalia` root and `Bucephala` was labeled `species`. The exact API response is retained at `outputs/ancestry-continuation-oct04/independent-flow-review/review-20261004T023940Z/ordinary_lineage.raw.json` (SHA-256 `35439cfb5e249f58ce91a7f897642788bb32b698c2811280a525fe8010e89a7e`).

The corrected projector checks each positional ancestor UUID against its own `core.taxon` row, including canonical name and compatible kingdom, before attaching that identity or its rank. Unverified names remain name-only with `unknown` rank. Misaligned arrays retain their raw values, mark the lineage `partial`, and do not transfer links across positions. The exact selected row is appended as the selected tip with its UUID, canonical name and rank; an inclusive final self-name is replaced by that verified tip. The response adds provenance and issue details while preserving the existing nested `tree` shape and top-level selected taxon fields.

The retained Fungi search capture supplies the selected `Schizophyllum commune` identity (`6db28640-67fb-4808-90de-956a856366f7`, kingdom `Fungi`, rank `species`) at `outputs/ancestry-continuation-oct04/independent-flow-review/review-20261004T023940Z/schizophyllum_search.raw.json` (SHA-256 `ef76f98bc4ed6dc2e65da2fb7a92694e28c21c8c9058ba149bfd6354f98b99f2`). That capture does not include a native lineage response; the regression uses its exact selected identity with local fixture lineage arrays and does not claim the fixture arrays reflect production state.

## FungiP identifier search correction

The dedicated `/api/mindex/taxa/collections/fungip` route already searches collection fields, but the Website-facing ordinary `/api/mindex/taxa?q=...` route filtered `q` only against core canonical/common names. That discarded a linked FungiP row before count/page and before post-page member enrichment. The ordinary route now resolves FungiP ID, ticker and DNA accession matches to core UUIDs before its existing core count and page query. Resolution uses the exact unique `core.taxon_external_id` crosswalk and rechecks the stored UUID, accepted name, kingdom and species rank; unresolved/conflicting source rows do not add taxa. Optional FungiP index errors remain nonfatal, and normal all-kingdom paging and the separate First40 launch association path are unchanged.

The captured FungiP result for `Schizophyllum commune` includes FG032, ticker `SPLIT`, accession `PZ955173.1` and the linked UUID above. No First40 launch values were used to make these matches.

## Validation executed

Project environment: Python 3.12.10; imports passed for psycopg 3.1.20, SQLAlchemy 2.0.49 and FastAPI 0.111.1.

- Focused project pytest: 36 passed across `test_publication_taxon_evidence.py`, `test_publication_evidence_insert_guard_sql_contract.py`, `test_genbank_taxon_linkage.py`, `test_wikipedia_source_provenance.py` and `test_wikimedia_commons_provenance.py`.
- Independent importer regression: 14/14 passed.
- Independent SQL guard-contract regression: 4/4 passed, including six faulty guard mutations rejected.
- Qualification-plan checks: 4/4 passed.
- Phylogeny focused regressions: 3 passed, including the retained Animalia reproduction, the retained Fungi selected identity, verified ancestor rank/name checks and misaligned-array handling.
- FungiP identifier-search regressions: 25 passed across the new parameterized ID/ticker/accession and pre-page tests, existing all-species listing/enrichment tests, and First40 snapshot projection tests (55 First40 cases skipped because local input bundles are absent).
- Isolated API/DB qualification: the actual `/api/mindex/phylogeny` route returned HTTP 200 against PostgreSQL 17.11 on a task-owned loopback cluster at port 15911. The local `core.taxon` fixture used the exact selected Animalia and Fungi IDs/names/ranks from the retained captures, verified the Animalia UUID was not attached to the `Animalia` root, and verified both exact selected taxa were returned as tips. The Fungi lineage array was a local fixture because no native Fungi lineage capture was retained. Result SHA-256 `70c1a21b56d33ab203086006ae4166c59460bfd6f6010fcbce6eb5d887317c7d`; output and PostgreSQL logs are under `outputs/phylogeny-lineage-projection-oct04/`. The isolated server has been stopped.
- Isolated FungiP search/API qualification: the actual `/api/mindex/taxa?q=...` route returned the exact linked FG032 taxon with total `1` for each of `FG032`, `SPLIT` and `PZ955173.1`. The task-owned PostgreSQL fixture used the exact selected UUID and captured identity/accession/ticker, with a locally seeded matching external-ID crosswalk. Result SHA-256 `e6c2775065950a26c75200bad1d32f2c81168b73f3d5809a4c482a32bb2ad7a5`; same isolated database and stopped server as above.
- Exact PostgreSQL qualification: PASS on PostgreSQL 17.11 in a task-owned loopback-only cluster. Database `ancestry_pub_evidence_review_a4f1c842594f4f71a59167ee09b320a6`; port 15910. Migration SHA-256 `fc3e9389371c199b06baa1f5bb1ee0af2a4328a70ada08284292cc0311af1c15`. The pinned script verified reviewer guards, candidate-to-terminal updates, immutable source/evidence identities and rollback; it returned `PASS: exact migration guards, reviewed dispositions, immutable identity and rollback verified`. Its transaction rolled back, leaving no `core` or `bio` fixture schema. The task-owned server is stopped.
- Qualification log SHA-256: `440d2d8fe6672f515eBAA10ABA22EDC9F421D26AD0973E056557BB7B31E3B547`.

The test cluster files and logs are in `outputs/ancestry-native-scientific-evidence-oct04/`. The PostgreSQL qualification script was not modified; no production or shared database was contacted.

## Remaining gates and rollback

This evidence qualifies importer and migration behavior in the bounded disposable PostgreSQL test only. It does not establish a live MINDEX taxon crosswalk, current provider availability, rights review, reviewed publication/taxon association, Website wiring, production migration, or deployment.

No production migration has been applied. If a later owner-approved staging apply fails before commit, roll back the transaction. If an applied environment requires rollback before any evidence is stored, first verify the evidence-table count is zero and use an owner-reviewed transaction to drop only the new trigger, functions and empty evidence table. Once evidence exists, preserve those records and use a forward repair; do not drop or delete provenance.

The four pre-existing `mindex_test*_utf8.txt` line-ending changes remain unstaged and are not part of this candidate.
