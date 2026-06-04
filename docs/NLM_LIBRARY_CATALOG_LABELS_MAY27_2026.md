# NLM Library — Per-File Catalog Labels (May 27, 2026)

**Status:** Implemented (schema + ETL + API + backfill)  
**Related:** `NLM_TRAINING_DATA_SOURCES.md`, `NLM_AUDIO_INGEST_STARTED_MAY27_2026.md`, Request 010/012

## Problem

Early ingest registered ~2,000 ESC-50 and growing MBARI rows with only `filename`, `source_id`, and thin JSON metadata. NLM / SINE acoustic library testing needs **every file** to carry:

- Human **title** and **description** (what it is, where it came from)
- **label_primary** / **label_secondary** (class, environment, fold)
- **source_name**, **source_url**, **origin_dataset_id** (aligned with NLM registry)
- **acoustic_environment** (`air`, `underwater`, `urban_air`, …)
- Organized **NAS path**: `Library/acoustic/{source}/{environment}/{label}/{file}.wav`

## Schema

Migration: `migrations/20260604_library_blob_labels_may27_2026.sql`

| Table | Purpose |
|-------|---------|
| `library.blob` | New columns: `title`, `description`, `label_*`, `acoustic_environment`, `source_name`, `source_url`, `origin_dataset_id`, `nlm_*`, `fold_id`, `training_split`, `locale`, `capture_time_utc` |
| `library.source` | Parent dataset catalog (one row per NLM source id) |

## Code

| Module | Role |
|--------|------|
| `mindex_etl/library/nlm_source_registry.py` | Canonical source metadata (ESC-50, MBARI, FSD50K, …) |
| `mindex_etl/library/catalog_record.py` | `CatalogRecord` + ESC-50 / MBARI builders |
| `mindex_etl/library/sources_esc50.py` | Parses `meta/esc50.csv` per clip |
| `mindex_etl/jobs/ingest_nlm_audio_p0.py` | Organized paths + full labels on register |
| `scripts/backfill_library_blob_labels.py` | Labels existing rows without re-download |
| `mindex_api/routers/library.py` | `GET /library/sources`, enriched `GET /library/blobs` filters |

## NAS layout (organized)

```
/mnt/nas/mindex/Library/acoustic/
  esc50/air/dog/1-100032-A-0.wav
  esc50/coastal_air/sea_waves/...
  mbari_pacific_sound/underwater/ambient_ocean_2khz_decimated/MARS_20180101.wav
```

Sidecar: `{file}.manifest.json` next to each WAV with full catalog + ffmpeg probe.

## Deploy (VM 189)

```bash
# On MINDEX VM after git pull
cat migrations/20260604_library_blob_labels_may27_2026.sql | docker exec -i mindex-postgres psql -U mindex -d mindex

# Backfill existing ESC-50 / MBARI
docker exec mindex-etl python scripts/backfill_library_blob_labels.py --source all

# New ingest (labeled + organized paths for new files)
docker exec mindex-etl python -m mindex_etl.jobs.ingest_nlm_audio_p0 \
  --sources esc50,mbari_pacific_sound --max-files-per-source 5000 --max-gb 200
```

Restart `mindex-api` after API router change.

## Verify

```sql
SELECT origin_dataset_id, label_primary, COUNT(*) 
FROM library.blob 
WHERE category = 'acoustic' 
GROUP BY 1, 2 
ORDER BY 1, 3 DESC;

SELECT COUNT(*) FILTER (WHERE title IS NULL) AS missing_title FROM library.blob;
```

API (with internal token):

- `GET /api/mindex/library/sources?category=acoustic`
- `GET /api/mindex/library/blobs?category=acoustic&label_primary=dog&limit=20`

## Next sources (P1)

- **fsd50k** — Zenodo wget + `collection_dev.csv` labels (no HuggingFace `datasets` on ETL CPU)
- **urbansound8k**, **sanctsound**, **noaa_nrs**, **watkins_whoi** — per registry in `nlm_source_registry.py`

## Gas / chemical blobs

Same pattern planned: `library.blob` category `gas` / `chemical` with `label_primary` = VOC class, `sensor_type` = BME688, etc.
