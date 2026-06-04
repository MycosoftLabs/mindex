# NLM Acoustic Ingest Started (May 27, 2026)

**Status:** In progress on MINDEX VM 189  
**NAS:** `/mnt/nas/mindex/Library/acoustic/{source_id}/` — must be a **CIFS mount** to 192.168.0.105, not a folder on the VM disk. See `docs/MINDEX_LIBRARY_NAS_MOUNT_MAY27_2026.md`.  
**Registry:** `library.blob`, `library.manifest`  
**Sources (P0):** `esc50`, `ds3500`, `mbari_pacific_sound`  
**Job:** `python -m mindex_etl.jobs.ingest_nlm_audio_p0`  
**ETL name:** `nlm_audio_p0` in `run_all --jobs nlm_audio_p0`

## API (live on 189)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/mindex/library/catalog` | Category counts + directory scan |
| `GET /api/mindex/library/blobs?category=acoustic` | Registered files for Library tab |
| `GET /api/mindex/library/blobs/{id}/stream` | Play audio in browser |
| `POST /api/mindex/library/import` | Start background ingest |

## Ops

```bash
# Log on VM
tail -f /home/mycosoft/mindex/logs/nlm_audio_ingest.log

# Re-run with higher cap (44TB NAS available)
docker compose exec -T api python -m mindex_etl.jobs.ingest_nlm_audio_p0 \
  --sources esc50,ds3500,mbari_pacific_sound \
  --max-files-per-source 50000 --max-gb 3000
```

## Codex frontend

Point Library tab at `GET /api/mindex/library/blobs?category=acoustic` and stream URLs on each row. Counts rise as ingest runs; no mock rows.
