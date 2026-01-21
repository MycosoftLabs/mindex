# MINDEX ETL Sync Guide

## Overview

The MINDEX ETL (Extract, Transform, Load) system automatically populates the fungal database from multiple sources across the internet. This guide covers how to use the enhanced sync system with checkpoint/resume and exponential backoff.

## Features

### ✅ Exponential Backoff Retry
- Automatically retries failed requests with exponential backoff (4s, 8s, 16s, 32s, 64s, 128s, 256s, max 300s)
- Handles rate limit errors (403, 429) gracefully
- Respects API rate limits (iNaturalist: 100 req/min, GBIF: 3 req/sec)

### ✅ Checkpoint/Resume
- Saves progress every 10 pages
- Can resume from last checkpoint after interruption
- Checkpoints stored in `/tmp/mindex_etl_checkpoints/`

### ✅ Data Volume Query
- Real-time database statistics
- Shows data by source, quality metrics, top taxa
- Available via Python script or PowerShell wrapper

## Quick Start

### Run Full Sync

```powershell
# Start comprehensive sync (runs in background)
docker run -d --name mindex-full-sync \
  --network mindex_mindex-network \
  -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex \
  mindex-api python /app/scripts/full_fungi_sync_v2.py
```

### Monitor Progress

```powershell
# Watch logs in real-time
docker logs -f mindex-full-sync-v2

# Or use the monitor script
.\scripts\monitor_sync.ps1
```

### Query Data Volume

```powershell
# Quick stats
.\scripts\query_data_volume.ps1

# With JSON output
.\scripts\query_data_volume.ps1 --json

# Or directly
docker run --rm --network mindex_mindex-network \
  -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex \
  mindex-api python /app/scripts/query_data_volume.py
```

## Data Sources

| Source | Expected Records | Update Frequency |
|--------|-----------------|------------------|
| **iNaturalist** | 50,000+ taxa, 100,000+ observations | Daily |
| **GBIF** | 50,000+ occurrences | Daily |
| **MycoBank** | 150,000+ names with synonyms | Weekly |
| **FungiDB** | 1,000+ genomes | Weekly |
| **Mushroom.World** | Traits and descriptions | Weekly |
| **Wikipedia** | Taxon descriptions | Weekly |

## Checkpoint System

### How It Works

1. **Automatic Checkpoints**: Saved every 10 pages during sync
2. **Resume on Restart**: Automatically detects and resumes from last checkpoint
3. **Manual Checkpoint**: Checkpoints saved in `/tmp/mindex_etl_checkpoints/`

### Resume After Interruption

If a sync is interrupted (container stopped, error, etc.), it will automatically resume from the last checkpoint when restarted:

```powershell
# The sync script automatically detects and resumes from checkpoints
docker run -d --name mindex-full-sync-v2 \
  --network mindex_mindex-network \
  -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex \
  mindex-api python /app/scripts/full_fungi_sync_v2.py
```

### Clear Checkpoints

```powershell
# Remove all checkpoints (start fresh)
docker exec mindex-full-sync-v2 rm -rf /tmp/mindex_etl_checkpoints/*
```

## Rate Limit Handling

### iNaturalist API
- **Limit**: 100 requests per minute
- **Our Rate**: ~85 requests/minute (safe margin)
- **Delay**: 0.7 seconds between requests
- **403/429 Handling**: Waits 60 seconds, then retries with exponential backoff

### GBIF API
- **Limit**: 3 requests per second
- **Our Rate**: ~3 requests/second
- **Delay**: 0.3 seconds between requests

## Individual Sync Jobs

### Sync iNaturalist Taxa

```powershell
docker run --rm --network mindex_mindex-network \
  -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex \
  mindex-api python -m mindex_etl.jobs.sync_inat_taxa --max-pages 100
```

### Sync iNaturalist Observations

```powershell
docker run --rm --network mindex_mindex-network \
  -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex \
  mindex-api python -m mindex_etl.jobs.sync_inat_observations --max-pages 50
```

### Sync GBIF Occurrences

```powershell
docker run --rm --network mindex_mindex-network \
  -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex \
  mindex-api python -m mindex_etl.jobs.sync_gbif_occurrences --max-pages 50
```

## Continuous Sync Scheduler

For automatic periodic updates:

```powershell
# Start scheduler (runs continuously)
docker compose --profile etl up -d etl

# Check scheduler logs
docker compose logs -f etl
```

The scheduler runs:
- iNaturalist observations: Every 6 hours
- GBIF occurrences: Daily
- Taxa updates: Daily
- MycoBank/FungiDB: Weekly

## Troubleshooting

### Rate Limit Errors

If you see `403 Forbidden` or `429 Too Many Requests`:

1. **Wait**: The system automatically waits and retries
2. **Check Logs**: `docker logs mindex-full-sync-v2`
3. **Resume**: The sync will resume from checkpoint after rate limit expires

### Deadlock Errors

If you see `deadlock detected`:

1. **Stop Parallel Syncs**: Only run one sync at a time
2. **Check Database**: Ensure no other processes are writing
3. **Retry**: The sync will automatically retry

### Container Stopped

If the container stops unexpectedly:

1. **Check Logs**: `docker logs mindex-full-sync-v2`
2. **Resume**: Restart the container - it will resume from checkpoint
3. **Check Database**: Verify data was saved before interruption

## Data Access

### Via API

```powershell
# Get taxa
curl.exe "http://localhost:8000/api/mindex/taxa?limit=10" `
  -H "X-API-Key: your-api-key"

# Get observations
curl.exe "http://localhost:8000/api/mindex/observations?limit=10" `
  -H "X-API-Key: your-api-key"
```

### Via Database

```powershell
# Direct database query
docker exec mindex-postgres psql -U mindex -d mindex -c "
  SELECT source, count(*) 
  FROM core.taxon 
  GROUP BY source;
"
```

## Performance

### Expected Sync Times

- **iNaturalist Taxa (50K)**: ~10-12 hours
- **iNaturalist Observations (100K)**: ~20-24 hours
- **GBIF Occurrences (50K)**: ~5-6 hours
- **MycoBank (150K)**: ~8-10 hours
- **Total Full Sync**: ~2-3 days

### Optimization Tips

1. **Run Overnight**: Full syncs are best run during off-peak hours
2. **Use Checkpoints**: Don't worry about interruptions - resume is automatic
3. **Monitor Progress**: Use `query_data_volume.py` to track growth
4. **Incremental Updates**: Use scheduler for daily updates instead of full syncs

## Next Steps

- ✅ Exponential backoff retry logic
- ✅ Checkpoint/resume system
- ✅ Data volume query script
- 🔄 Continuous sync scheduler (optional)
- 📊 Advanced analytics dashboard (future)
