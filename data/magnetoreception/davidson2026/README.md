# Davidson 2026 source compilation

User-supplied import package for Zenodo record24571664 v2, DOI10.5281/zenodo.24571664.
Original CSV and cleaned entries are retained with SHA-256 pins in the importer.
License and attribution are included in each imported trait metadata record.
Author classifications are source-reported, not independent scientific validation.

Run `python -m mindex_etl.jobs.sync_magnetoreception_davidson2026 --dry-run` with the
approved production MINDEX connection through AWS. This executes the same DML
inside a transaction and rolls it back. No DELETE or schema-changing SQL exists.
Existing taxa are not updated. A transaction advisory lock prevents overlapping
runs of this dataset; a pre-existing publication ID is retained for links.

The expected source preview separates roughly934 existing links,108 new named
species,198 unresolved group labels,10 ambiguous names and26 excluded review or
mixed-kingdom rows. `unlinked` includes all three unresolved/excluded categories;
it is not the group-label count. Actual production counts must be recorded before
committing. Run a second committed pass to confirm zero added taxa, traits and
publication links. The publication metadata may be refreshed without adding rows.

Six focused offline tests pass. Production dry-run/commit receipts are separate;
this source document is not a claim that ingestion has happened.
