# Native scientific linkage remediation — 2026-10-04

## Source pin and scope

This review candidate is based on frozen source commit `9e902613d192f4aeee7f60dd30d31f558d3221ed` and adds the independent-review fixes in commit `00f6d78ce5a7df456946ef1444ac48c720c70450`, branch `codex/ancestry-scientific-evidence-oct04`. The complete review branch is a clean diff from `origin/main` at `8da30115ca3aac6b67683138e02376bcde504a8f`.

The remediation changes only the publication evidence importer, its additive evidence migration and focused regression tests. It does not alter `bio.publication_taxon`, create reviewed species-publication links, fetch providers, touch Cursor-owned all-species loaders, change Website code, or apply schema/data changes outside the isolated qualification database.

## Behavior corrected

- Validate accession.version and source-content SHA-256 before interpreting a GenBank response, including records with no citable references. Wrong identity pins return without database calls.
- Distinguish newly inserted evidence from an idempotent existing row. Read back and return the persisted evidence UUID and candidate/accepted/rejected review state; a missing readback fails so the caller can roll back.
- Preserve mixed per-row review dispositions in the receipt. The importer never promotes or overwrites an existing disposition and does not create a `bio.publication_taxon` link.
- Require a nonblank reviewer for terminal dispositions on both INSERT and UPDATE. Include `evidence_id` in the immutable source-identity guard.

## Validation executed

Project environment: Python 3.12.10; imports passed for psycopg 3.1.20, SQLAlchemy 2.0.49 and FastAPI 0.111.1.

- Focused project pytest: 36 passed across `test_publication_taxon_evidence.py`, `test_publication_evidence_insert_guard_sql_contract.py`, `test_genbank_taxon_linkage.py`, `test_wikipedia_source_provenance.py` and `test_wikimedia_commons_provenance.py`.
- Independent importer regression: 14/14 passed.
- Independent SQL guard-contract regression: 4/4 passed, including six faulty guard mutations rejected.
- Qualification-plan checks: 4/4 passed.
- Exact PostgreSQL qualification: PASS on PostgreSQL 17.11 in a task-owned loopback-only cluster. Database `ancestry_pub_evidence_review_a4f1c842594f4f71a59167ee09b320a6`; port 15910. Migration SHA-256 `fc3e9389371c199b06baa1f5bb1ee0af2a4328a70ada08284292cc0311af1c15`. The pinned script verified reviewer guards, candidate-to-terminal updates, immutable source/evidence identities and rollback; it returned `PASS: exact migration guards, reviewed dispositions, immutable identity and rollback verified`. Its transaction rolled back, leaving no `core` or `bio` fixture schema. The task-owned server is stopped.
- Qualification log SHA-256: `440d2d8fe6672f515eBAA10ABA22EDC9F421D26AD0973E056557BB7B31E3B547`.

The test cluster files and logs are in `outputs/ancestry-native-scientific-evidence-oct04/`. The PostgreSQL qualification script was not modified; no production or shared database was contacted.

## Remaining gates and rollback

This evidence qualifies importer and migration behavior in the bounded disposable PostgreSQL test only. It does not establish a live MINDEX taxon crosswalk, current provider availability, rights review, reviewed publication/taxon association, Website wiring, production migration, or deployment.

No production migration has been applied. If a later owner-approved staging apply fails before commit, roll back the transaction. If an applied environment requires rollback before any evidence is stored, first verify the evidence-table count is zero and use an owner-reviewed transaction to drop only the new trigger, functions and empty evidence table. Once evidence exists, preserve those records and use a forward repair; do not drop or delete provenance.

The four pre-existing `mindex_test*_utf8.txt` line-ending changes remain unstaged and are not part of this candidate.
