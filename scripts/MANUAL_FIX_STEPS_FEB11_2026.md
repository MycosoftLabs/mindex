# Manual MINDEX Fix Steps - February 11, 2026

## Current Status

✅ **Database Connection**: Working (`"db": "ok"`)  
❌ **Data Endpoints**: Returning 500 errors  
❌ **Tables**: May be empty or missing

## Quick Manual Fix (5 minutes)

### Step 1: SSH to MINDEX VM

```bash
ssh mycosoft@192.168.0.189
# Enter password when prompted
```

### Step 2: Check Docker Containers

```bash
cd /home/mycosoft/mindex
docker compose ps

# Should show:
# - mindex-postgres (running)
# - mindex-redis (running)
# - mindex-qdrant (running)
# - mindex-api (running)
```

### Step 3: Check Database Tables

```bash
# Connect to database
docker exec -it mindex-postgres psql -U mindex -d mindex

# Check if schemas exist
\dn

# Should show: core, obs, bio, telemetry, ledger, app

# Check if tables exist
\dt obs.*

# Should show: obs.observation table

# Count records
SELECT COUNT(*) FROM obs.observation;
SELECT COUNT(*) FROM core.taxon;

# Exit psql
\q
```

### Step 4: If Tables Are Empty - Run Migration

```bash
cd /home/mycosoft/mindex

# Run all migrations
docker exec -it mindex-postgres psql -U mindex -d mindex < migrations/0001_init.sql

# Or if using Alembic
docker compose exec mindex-api alembic upgrade head
```

### Step 5: Check API Logs for Errors

```bash
# View recent logs
docker logs mindex-api --tail 50

# Watch live logs
docker logs -f mindex-api

# Look for:
# - Database connection errors
# - Missing table errors
# - API key validation errors
```

### Step 6: Test API Endpoints Directly

```bash
# Health (should work)
curl http://localhost:8000/api/mindex/health

# Stats (failing with 500)
curl http://localhost:8000/api/mindex/stats

# Observations (failing with 500)
curl "http://localhost:8000/api/mindex/observations?limit=3"

# Taxa (try this too)
curl "http://localhost:8000/api/mindex/taxa?limit=5"
```

### Step 7: Check .env File

```bash
cat /home/mycosoft/mindex/.env

# Should have:
DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex
MINDEX_DB_HOST=mindex-postgres
MINDEX_DB_PORT=5432
MINDEX_DB_USER=mindex
MINDEX_DB_PASSWORD=mindex
MINDEX_DB_NAME=mindex

# API Keys
MINDEX_API_KEY=local-dev-key
API_KEYS=["local-dev-key"]
```

### Step 8: Restart All Containers

```bash
cd /home/mycosoft/mindex

# Full restart
docker compose down
docker compose up -d

# Wait 15 seconds
sleep 15

# Check status
docker compose ps
curl http://localhost:8000/api/mindex/health
curl http://localhost:8000/api/mindex/stats
```

## Common Issues & Fixes

### Issue 1: Tables Don't Exist

```bash
# Run init migration
docker exec -it mindex-postgres psql -U mindex -d mindex < migrations/0001_init.sql
```

### Issue 2: API Key Authentication Failing

The website sends `X-API-Key: local-dev-key`. Ensure MINDEX accepts this:

```bash
# Edit .env
nano /home/mycosoft/mindex/.env

# Add or update:
MINDEX_API_KEY=local-dev-key
API_KEYS=["local-dev-key"]

# Restart API
docker compose restart mindex-api
```

### Issue 3: Database Has No Data

If tables exist but are empty, run ETL sync:

```bash
cd /home/mycosoft/mindex

# Sync taxa from GBIF
docker compose run --rm mindex-etl python -m mindex_etl.jobs.sync_gbif_taxa --limit 1000

# Or use the API sync endpoint from local machine:
curl -X POST http://192.168.0.189:8000/api/sync/gbif
```

### Issue 4: Wrong Docker Network

```bash
# Check network
docker network inspect mindex_mindex-network

# Should show all 4 containers attached

# If not, recreate with compose
cd /home/mycosoft/mindex
docker compose down
docker compose up -d
```

## Verification Commands (From Windows)

```powershell
# Health check
Invoke-RestMethod http://192.168.0.189:8000/api/mindex/health | ConvertTo-Json

# Stats (should work after fix)
Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats | ConvertTo-Json

# Observations (should work after fix)
Invoke-RestMethod "http://192.168.0.189:8000/api/mindex/observations?limit=3" | ConvertTo-Json -Depth 3

# Website integration
Invoke-RestMethod http://localhost:3010/api/natureos/mindex/stats | ConvertTo-Json
```

## Expected Success Output

### Health Endpoint
```json
{
  "status": "ok",
  "db": "ok",
  "service": "mindex",
  "version": "0.2.0"
}
```

### Stats Endpoint
```json
{
  "total_taxa": 5529,
  "total_observations": 2491,
  "observations_with_location": 1234,
  "etl_status": "idle",
  "genome_records": 0
}
```

### Observations Endpoint
```json
{
  "data": [ ...array of observations... ],
  "pagination": {
    "limit": 3,
    "offset": 0,
    "total": 2491
  }
}
```

## After Fix - Test These Pages

1. http://localhost:3010/natureos/mindex - All tabs should work
2. http://localhost:3010/natureos/mindex/explorer - Map with pins
3. http://localhost:3010/mindex - Public portal with live stats

---

**Priority**: HIGH  
**Estimated Time**: 5-10 minutes  
**Required Access**: SSH to 192.168.0.189 (password required)
