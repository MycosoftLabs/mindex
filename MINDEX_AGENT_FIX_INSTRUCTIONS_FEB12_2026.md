# MINDEX Agent Fix Instructions - February 12, 2026

## Current Status

| Component | Status | Details |
|-----------|--------|---------|
| MINDEX API | ✅ Running | `http://192.168.0.189:8000` |
| Database Connection | ✅ Connected | PostgreSQL responding |
| Taxa Data | ✅ Has Data | 35,000 taxa (25K iNaturalist + 10K GBIF) |
| Observations | ❌ EMPTY | 0 observations - **THIS NEEDS FIXING** |
| Genome Records | ❌ Empty | 0 records |
| Trait Records | ❌ Empty | 0 records |

## Problem

The MINDEX database has **taxa** but **no observations**. This means:
- Species Explorer map shows nothing (no geo coordinates)
- Live stats show 0 observations
- Data visualizations are empty

## Fix Instructions

### Step 1: SSH to MINDEX VM

```bash
ssh mycosoft@192.168.0.189
cd /home/mycosoft/mindex
```

### Step 2: Check Current Database State

```bash
# Check observations table
docker exec -it mindex-postgres psql -U mindex -d mindex -c "SELECT COUNT(*) FROM obs.observation;"

# Check if obs schema exists
docker exec -it mindex-postgres psql -U mindex -d mindex -c "\dt obs.*"

# If schema doesn't exist, run migrations
docker exec -it mindex-postgres psql -U mindex -d mindex -f /docker-entrypoint-initdb.d/migrations/0001_init.sql
```

### Step 3: Sync Observations from GBIF/iNaturalist

**Option A: Via API (if sync endpoint exists)**
```bash
curl -X POST "http://localhost:8000/api/sync/observations?limit=5000"
```

**Option B: Via ETL Job (recommended)**
```bash
# Run observation sync job
docker compose run --rm mindex-etl python -m mindex_etl.jobs.sync_observations --limit 5000

# OR if using scripts directory
python scripts/sync_observations.py --source gbif --limit 5000
python scripts/sync_observations.py --source inaturalist --limit 5000
```

**Option C: Direct GBIF Import**
```bash
# Download and import GBIF occurrence data for fungi
docker compose run --rm mindex-etl python -m mindex_etl.sources.gbif_occurrences --kingdom Fungi --limit 10000
```

### Step 4: Verify Data Was Imported

```bash
# Check observation count
docker exec -it mindex-postgres psql -U mindex -d mindex -c "SELECT COUNT(*) FROM obs.observation;"

# Check observations with coordinates
docker exec -it mindex-postgres psql -U mindex -d mindex -c "SELECT COUNT(*) FROM obs.observation WHERE latitude IS NOT NULL;"

# Test API endpoint
curl "http://localhost:8000/api/mindex/observations?limit=5"
```

### Step 5: Restart API (to clear any caches)

```bash
docker compose restart mindex-api
sleep 10
curl http://localhost:8000/api/mindex/stats
```

## Expected Results After Fix

```json
{
  "total_taxa": 35000,
  "total_observations": 5000,      // Should be > 0
  "observations_with_location": 4500,  // Should be > 0
  "observations_with_images": 2000,
  "taxa_with_observations": 1500
}
```

## If Observation Table Doesn't Exist

Run the migration that creates the obs schema:

```bash
# Check if migration file exists
ls -la migrations/

# Run the observation migration (usually 0002 or similar)
docker exec -it mindex-postgres psql -U mindex -d mindex -f migrations/0002_observations.sql

# Or run all pending migrations
alembic upgrade head
```

## Alternative: Import Sample Data for Testing

If full sync is slow, import a small sample:

```bash
# Create sample observations directly
docker exec -it mindex-postgres psql -U mindex -d mindex << 'EOF'
INSERT INTO obs.observation (taxon_id, latitude, longitude, observed_on, source, external_id)
SELECT 
    t.id,
    (random() * 180) - 90 as latitude,
    (random() * 360) - 180 as longitude,
    NOW() - (random() * interval '365 days') as observed_on,
    'sample' as source,
    'sample_' || generate_series(1, 1000) as external_id
FROM core.taxon t
ORDER BY random()
LIMIT 1000;
EOF
```

## Troubleshooting

### Error: "relation obs.observation does not exist"
- Run migrations: `alembic upgrade head` or manually run SQL files in `migrations/`

### Error: "permission denied for schema obs"
- Grant permissions: `GRANT ALL ON SCHEMA obs TO mindex;`

### Error: "connection refused"
- Check if postgres is running: `docker compose ps`
- Restart: `docker compose restart postgres`

### Sync job hangs
- Check logs: `docker compose logs -f mindex-etl`
- May need to set API keys for GBIF/iNaturalist in `.env`

## Contact

If you encounter issues not covered here:
1. Check logs: `docker compose logs -f`
2. Check the ETL job code in `mindex_etl/jobs/`
3. Check the migration files in `migrations/`

---

**Priority**: HIGH
**Time to fix**: 10-30 minutes depending on data sync speed
**Result**: Species Explorer map will show observation pins, stats will populate
