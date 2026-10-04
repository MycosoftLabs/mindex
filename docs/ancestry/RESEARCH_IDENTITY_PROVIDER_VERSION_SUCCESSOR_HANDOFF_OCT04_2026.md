# Native research identity provider-version successor handoff

## Publication scope

This is a source-qualified, review-only successor to the existing head of [MINDEX PR #35](https://github.com/MycosoftLabs/mindex/pull/35). PR #35 is currently open as a draft at head `e9b740327da80f2b28a36635e7235734bbd026a9`; its target branch is `codex/ancestry-scientific-evidence-oct04` at `a60f2f309c438f375313f8c843877dc9881aaf7b`. The successor branch `codex/research-identity-provider-versions-oct04` is based on PR #35's head so its follow-up diff stays limited to the producer correction, tests, retained-capture fixture, and documentation. PR #35's source and receipts remain unchanged.

The producer implementation pin is commit `97e865d93f38156acd2141769ceacecc7ebf8361`, with `mindex_etl/research_identity_export.py` SHA-256 `93cae80dca18b979e8f723e1d8cfcec8dd2ad5d2fef73bed8238b339ac08caea` and query SHA-256 `bccd6bbfb3192df45c995b0508beb4932da625ecee0201424bda1d6dae454fde`. The provider/custody addendum was corrected through documentation commit `c53b20fd729110c2694b0b09e146bbe4e48fa7c4`; its SHA-256 at that commit is `f93c48db994de8827f0224bbc60b3841e155509c548c15f103b8ff65178801cc`. This handoff is an additional portable summary and does not revise the earlier receipt, frozen PR #35 pins, or source commit.

## Producer behavior

- **Ensembl:** emit `ensembl.stable_id_version` only when stored `metadata.ensembl_source_record` has an exact `id` matching the stored accession, a positive integer `version` whose decimal form matches the stored version, a `molecule` matching the stored `dna` or `protein` sequence type, and a `seq` whose UTF-8 SHA-256 equals the database-computed sequence hash. Missing or mismatched evidence remains unavailable/conflicted and ineligible.
- **Retained Ensembl record:** YAL001C is a real retained sequence capture with `id=YAL001C`, `version=null`, `molecule=dna`, 3,573 sequence bytes, and SHA-256 `eaa6315892b1bc99614326d70a0aae0b5d752300d44e2aa9c734e620b2bf676d`. It is a negative boundary only; no real positive versioned Ensembl record has been qualified. Positive Ensembl DNA/protein rows in the paired receipt are synthetic API-shaped controls.
- **BOLD and UNITE:** remain `version_namespace_unsupported`. No retained BOLD record was available. For UNITE, no retained evidence links the stored sequence accession and exact sequence bytes to a versioned SH accession. Do not infer a provider version from a BOLD Process ID, catalog release, or identifier alone.
- **Zero-byte sequence:** preserve the empty-sequence digest `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` and exact UTF-8 hash scope, emit `sequence_empty`, mark the row ineligible, and leave export coverage `partial`. The consumer's positive-byte guard remains required.

## Paired acceptance evidence

An independent frozen pair check exercised the committed producer at `97e865d93f38156acd2141769ceacecc7ebf8361` against the frozen consumer at `58a8847986409e7703436082ac021b6fabf5c380`. The consumer source `research_jobs/identity_v2.py` is pinned at SHA-256 `696e5ec330214b1076c41aba0ab4ebc32b66b528940ad2d06455f481a190ece2`; request-admission SHA-256 is `1a08f3520f493bd5239716a779b42a7a96a1520fd7e4a587549477d6f27e4348`. The consumer query pin is the producer query SHA above.

The frozen receipt is `D:\Users\admin2\Desktop\MYCOSOFT\CODE\outputs\ancestry-continuation-oct04\native-identity-export-qualification\paired-93ca-58a-independent\FROZEN_PAIRED_ACCEPTANCE_RECEIPT.json`, SHA-256 `3f023878599258c31eb6e86ade0a5fdf97330fefe2254609978c643c9ae25ce8`. Its companion summary `PAIRED_FROZEN_ACCEPTANCE.md` is SHA-256 `b92b39602ac5c10774a59e6ebd2a7872a5f8b8371fead108aa9bfc7d6e669131`; `EVIDENCE_MANIFEST.json` is SHA-256 `820f490af35ffe154bf37a43a3173a1536ddc59036e86795ce2f7e3b3ec603c4` and covers 61 files.

All eight finite controls passed on one new disposable PostgreSQL 17.11 cluster at loopback port `55486`, system identifier `7692693264193800888`. The cluster stopped successfully; postmaster PID, listener, and PID file are absent, and its data/logs are retained. Each producer read used repeatable-read/read-only transactions and the fixed pinned query, then rolled back to `IDLE`. The eight controls covered the empty ITS row; retained UniProt version 2; wrong UniProt entry version, stored hash, and molecule conflicts; synthetic Ensembl DNA and protein records; and the retained YAL001C null-version negative case.

For the empty DNA row, the actual module boundary returned consumer status `association_unavailable`, no record, and `coverage_complete=false`. This proves the paired component outcome only; no HTTP route or HTTP 503 status was tested. UniProt P00549's retained 500-byte sequence and `sequenceVersion:2` are public-capture evidence, while its canonical taxon/crosswalk in the database fixture is synthetic. These eight checks do not establish live canonical authority, provider acquisition, biological accuracy, production persistence, full MAS qualification, deployment, or shared-database behavior. No shared database, database `189`, AWS, or provider sequence endpoint was contacted.

Focused offline producer checks recorded against commit `97e865d` were:

```text
python -m pytest -q tests/test_research_identity_export.py -k 'ensembl or zero_byte_sequence'  -> 10 passed
python -m pytest -q tests/test_research_identity_export.py -k 'uniprot'                         -> 3 passed
```

The earlier 33-test suite was not repeated. The paired acceptance used its already-sealed receipt; do not reopen or reuse its retained private cluster. The provider/custody addendum contains the fuller official-doc evidence and Cursor's acquisition qualification steps: [provider-version review](RESEARCH_IDENTITY_PROVIDER_VERSION_REVIEW_OCT04_2026.md).

## Integration boundary and next owner actions

The current PR #35 consumer snapshot is pinned to the older producer source SHA `7a29cd8e4b2fed7cc7f4a2b93ea8771e56c565c3e1962868547b5568e85b1c9a` and the same query SHA. Do not imply that this old frozen consumer accepts the new producer behavior. The paired review instead used consumer `58a8847` with producer `97e865d`; a coordinated consumer/scanner successor is a separate change and is owned outside this producer lane. Before Website or any consumer integrates the new metadata convention, the research owner must review both the Ensembl metadata shape and `sequence_empty` partial-coverage behavior.

Cursor may use the acquisition plan in the provider-version review to obtain a real retained Ensembl stable-ID capture, plus qualifying BOLD or UNITE evidence, only after selecting authorized source records and a read-only authority. Until a real Ensembl response is pinned to a matching stored row, positive Ensembl tests remain synthetic and BOLD/UNITE remain unsupported. No migration, apply, production/shared database access, privilege change, deployment, provider acquisition, or live identity authority is requested by this PR.
