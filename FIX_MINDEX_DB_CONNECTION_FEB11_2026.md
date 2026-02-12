# Fix MINDEX Database Connection - February 11, 2026

## Current Issue

MINDEX API is running on **192.168.0.189:8000** but **PostgreSQL database is not connected**:

```json
{
  "status": "ok",
  "db": "error",  // ← Database connection failed!
  "service": "mindex",
  "version": "0.2.0"
}
```

This causes:
- ❌ Species Explorer shows "No species data available"
- ❌ All data pipeline endpoints return 500 errors
- ❌ Taxa/observations endpoints fail
- ❌ Dashboard shows everything "offline"

## Root Cause

The MINDEX API container cannot connect to PostgreSQL. Common causes:
1. PostgreSQL container not running
2. Wrong database credentials in `.env`
3. Docker network misconfiguration
4. Database not initialized

## Fix Steps

### Option 1: SSH and Restart (Manual)

```bash
# SSH to MINDEX VM
ssh mycosoft@192.168.0.189

# Check running containers
docker ps -a

# Check if postgres is running
docker ps | grep postgres
# If not running:
docker start mindex-postgres

# Or restart entire stack
cd /home/mycosoft/mindex
docker-compose down
docker-compose up -d

# Wait 10 seconds for startup
sleep 10

# Check health
curl http://localhost:8000/api/mindex/health
# Should show: "db": "ok"

# Test data endpoint
curl "http://localhost:8000/api/mindex/observations?limit=5"
```

### Option 2: Use Local Python Script

```powershell
# From local machine (requires correct VM password)
cd C:\Users\admin2\Desktop\MYCOSOFT\CODE\MINDEX\mindex

# Set password
$env:VM_PASSWORD = "YOUR_ACTUAL_VM_PASSWORD"

# Run fix script
python _fix_mindex_deploy.py

# Or check health
python _check_mindex_health.py
```

### Option 3: Docker Compose Fix

```bash
# SSH to MINDEX VM
ssh mycosoft@192.168.0.189

cd /home/mycosoft/mindex

# Stop everything
docker-compose down

# Remove volumes (CAUTION: This deletes data!)
# docker-compose down -v

# Start fresh
docker-compose up -d

# Watch logs
docker-compose logs -f mindex-api
docker-compose logs -f mindex-postgres

# In another terminal, check health
watch curl -s http://localhost:8000/api/mindex/health
```

## Environment Variables Check

Ensure `/home/mycosoft/mindex/.env` has correct settings:

```env
# Database Connection
DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex
MINDEX_DB_HOST=mindex-postgres  # ← Must match docker-compose service name
MINDEX_DB_PORT=5432
MINDEX_DB_USER=mindex
MINDEX_DB_PASSWORD=mindex
MINDEX_DB_NAME=mindex

# Redis
REDIS_URL=redis://mindex-redis:6379

# Qdrant
QDRANT_URL=http://mindex-qdrant:6333

# API
API_HOST=0.0.0.0
API_PORT=8000
API_LOG_LEVEL=info
MINDEX_API_KEY=local-dev-key
```

## Docker Network Check

```bash
# Check if containers are on same network
docker network ls | grep mindex

# Inspect network
docker network inspect mindex_mindex-network

# Ensure all containers are attached:
# - mindex-api
# - mindex-postgres
# - mindex-redis  
# - mindex-qdrant
```

## Database Initialization

If database is empty or not initialized:

```bash
# SSH to MINDEX VM
cd /home/mycosoft/mindex

# Run migrations
docker-compose exec mindex-api python -m alembic upgrade head

# Or initialize from scratch
docker-compose exec mindex-postgres psql -U mindex -d mindex < migrations/init.sql
```

## Quick Verification

From **local machine**:

```powershell
# 1. Check API is reachable
Invoke-RestMethod http://192.168.0.189:8000/api/mindex/health | ConvertTo-Json

# Expected: "db": "ok"

# 2. Test observations endpoint
Invoke-RestMethod "http://192.168.0.189:8000/api/mindex/observations?limit=3" | ConvertTo-Json -Depth 3

# Should return array of observations

# 3. Test website integration
Invoke-RestMethod http://localhost:3010/api/natureos/mindex/stats | ConvertTo-Json

# Should show taxa counts
```

## After Fix - Website Should Show

✅ `/natureos/mindex` - All dashboard sections working
✅ `/natureos/mindex/explorer` - Map with observation pins
✅ `/mindex` - Public portal with live stats
✅ Data Pipeline section shows "online" status

## Troubleshooting

### Still showing "offline"?

1. **Check MINDEX API logs:**
   ```bash
   docker logs mindex-api --tail 50
   ```

2. **Check PostgreSQL logs:**
   ```bash
   docker logs mindex-postgres --tail 50
   ```

3. **Test direct database connection:**
   ```bash
   docker exec -it mindex-postgres psql -U mindex -d mindex -c "SELECT COUNT(*) FROM taxa;"
   ```

### API Key Issues?

The website API routes use `local-dev-key` by default. Ensure MINDEX accepts this:

```env
# In /home/mycosoft/mindex/.env
MINDEX_API_KEY=local-dev-key
# Or
API_KEYS=["local-dev-key","another-key"]
```

### Port 8000 in Use?

```bash
# Check what's using port 8000
sudo netstat -tlnp | grep 8000

# Kill if needed
sudo fuser -k 8000/tcp
```

## Contact Information

If unable to resolve:
1. Check VM is accessible: `ping 192.168.0.189`
2. Check port is open: `Test-NetConnection -ComputerName 192.168.0.189 -Port 8000`
3. SSH password may need reset if authentication fails
4. Check Proxmox console if SSH is down

## Related Files

- MINDEX docker-compose: `/home/mycosoft/mindex/docker-compose.yml`
- MINDEX .env: `/home/mycosoft/mindex/.env`
- API code: `/home/mycosoft/mindex/mindex_api/`
- Migrations: `/home/mycosoft/mindex/migrations/`

---

**Status**: Database connection failure on MINDEX VM
**Priority**: HIGH - Blocks all MINDEX functionality
**Fix Time**: ~5 minutes once SSH access restored
