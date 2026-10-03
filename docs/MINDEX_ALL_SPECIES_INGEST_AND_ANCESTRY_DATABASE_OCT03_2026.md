# MINDEX all-species ingest and ancestry database API — October 3, 2026

**Date:** October 3, 2026
**Status:** Shipped (API + job). Ingest results are in `CODE/docs/MINDEX_ALL_SPECIES_INGEST_AND_ANCESTRY_DATABASE_OCT03_2026.md`.

## Why

`/natureos/ancestry/database` showed 3,680 species. That number is `rank = 'species' AND canonical_name ILIKE 'A%'`: the page's default letter "A", under an exact rank filter.

Two things kept the real total small:

- **Rank spelling.** MycoBank stores species as `sp.`, so 406,523 MycoBank species never matched `rank = 'species'`.
- **Missing sources.** MINDEX held about 30.6k GBIF and 12.9k iNaturalist species. GBIF's Backbone has millions of accepted species and iNaturalist about 590k. The old GBIF job crawled the search API, which stops at offset 100,000.

## API changes (`mindex_api/routers/taxon.py`)

- **Rank aliases.** `GET /api/mindex/taxa?rank=species` matches `species` and `sp.`. The same applies to the other ranks: genus/`gen.`, family/`fam.`, subsp., var., f., sect., and so on.
- **Fast list path.** It filters, sorts and pages directly on `core.taxon`. Per-taxon counts (observations, media, genomes, ...) are computed only for the returned page.
  - The old path still runs if the fast path fails.
  - The popular sort uses the stored `metadata.observations_count`.
- **Cached totals.** Totals are cached for 5 minutes per filter in a bounded cache of 1,024 entries.
- **Prefix filter** uses `lower(canonical_name) LIKE 'abc%'`, which can use an index.
- **Search escaping.** `q` and `prefix` escape the LIKE wildcards `%` and `_`.
- **New `GET /api/mindex/taxa/stats`.** It returns real stored species totals (species plus `sp.`):
  - `by_kingdom`
  - `by_primary_source`: the source that created the row
  - `by_linked_source`: species linked to each source through `core.taxon_external_id`
  - `taxa_total`
  - Results are cached for 5 minutes.

## Indexes (`migrations/20261003_taxon_all_species_indexes.sql`)

These are built `CONCURRENTLY` on the live DB, because migrations are only initdb-mounted:

- `lower(canonical_name) text_pattern_ops`
- `(rank, canonical_name)`
- `core.taxon_external_id (taxon_id, source)`
- Trigram GIN on `canonical_name` and `common_name`

## Bulk ingest job (`mindex_etl/jobs/bulk_taxonomy_ingest.py`)

The job ingests taxonomy only, never occurrences. It reads public Darwin Core Archives:

| Source | URL | Size |
|---|---|---|
| GBIF Backbone (accepted species) | `https://hosted-datasets.gbif.org/datasets/backbone/current/backbone.zip` | ~971 MB |
| iNaturalist taxonomy | `https://www.inaturalist.org/taxa/inaturalist-taxonomy.dwca.zip` | ~81 MB |

Each source is a single bulk download, resumable with HTTP Range, so there is no per-record crawling.

Per batch of 5,000 source IDs, the job runs these steps in one transaction:

1. Match on the crosswalk `core.taxon_external_id (source, external_id)`, which is the idempotency key.
2. Otherwise, match by name against an existing species-rank taxon. The kingdom must match, or the existing row must be `Undesignated` and the name must not be a cross-kingdom homonym in the source.
3. Enrich matched rows, filling only empty values: null/`Undesignated` kingdom, lineage shorter than 2, author, common name, the source's external ID, and `metadata.<source>_taxonomy`. A one-element legacy lineage is preserved in `metadata.legacy_lineage`.
4. Insert genuinely new species.
5. Write crosswalk rows (`ON CONFLICT DO NOTHING`).

Safety:

- Nothing is deleted, capped or sampled.
- Staging uses session TEMP tables, which are not WAL-logged and not in the `aws_mig` publication.
- The job pauses while any replication slot lags more than 1 GB, or while free disk is under 20 GB.
- A checkpoint is written after every committed batch, so a re-run resumes where it stopped.

### Run on MINDEX VM 189

```bash
mkdir -p ~/taxonomy-ingest
docker run -d --name mindex-taxonomy-ingest-gbif --network mindex_mindex-network \
  --env-file ~/mindex/.env -v ~/mindex/mindex_etl:/app/mindex_etl:ro \
  -v ~/taxonomy-ingest:/data --memory 2g --restart on-failure:5 \
  mindex-api python -m mindex_etl.jobs.bulk_taxonomy_ingest --source gbif --data-dir /data
```

For iNaturalist, use `--source inat` and the container name `mindex-taxonomy-ingest-inat`. Run iNaturalist after GBIF, not in parallel.

To smoke-test first, add `--max-batches 1`.

### Monitor

```bash
docker logs --tail 20 mindex-taxonomy-ingest-gbif
cat ~/taxonomy-ingest/checkpoint_gbif.json   # last_source_id / max_source_id / totals / status
curl -s -H "X-API-Key: $MINDEX_API_KEY" http://192.168.0.189:8000/api/mindex/taxa/stats
```

`status` in the checkpoint is `running`, `paused` (stopped by `--max-batches`) or `complete`.
