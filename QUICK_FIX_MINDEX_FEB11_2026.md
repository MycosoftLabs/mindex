# Quick Fix MINDEX Database - February 11, 2026

## 🔴 Problem

MINDEX API is running but **PostgreSQL is not connected**:
- API reachable at http://192.168.0.189:8000
- Database status: `"db": "error"`
- All data endpoints returning 500 errors

## ✅ Quick Fix (2 minutes)

### Option 1: SSH Manual Restart (Recommended)

```bash
# 1. SSH to MINDEX VM
ssh mycosoft@192.168.0.189
# Password: [your VM password]

# 2. Go to MINDEX directory
cd /home/mycosoft/mindex

# 3. Restart all containers
docker compose restart

# 4. Wait 10 seconds
sleep 10

# 5. Check health (should show "db": "ok")
curl http://localhost:8000/api/mindex/health

# 6. Test data endpoint
curl "http://localhost:8000/api/mindex/observations?limit=3"
```

### Option 2: Restart Just PostgreSQL

```bash
ssh mycosoft@192.168.0.189
cd /home/mycosoft/mindex
docker compose restart mindex-postgres
sleep 5
docker compose restart mindex-api
sleep 10
curl http://localhost:8000/api/mindex/health
```

### Option 3: From Windows PowerShell

```powershell
# Run the automated script (will prompt for password)
cd C:\Users\admin2\Desktop\MYCOSOFT\CODE\MINDEX\mindex\scripts
.\restart-mindex-vm.ps1
```

## ✅ Verification

After restart, check these endpoints:

```powershell
# From local machine:

# 1. Health check (should show "db": "ok")
Invoke-RestMethod http://192.168.0.189:8000/api/mindex/health | ConvertTo-Json

# 2. Stats (should show taxa count)
Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats | ConvertTo-Json

# 3. Observations (should return data)
Invoke-RestMethod "http://192.168.0.189:8000/api/mindex/observations?limit=3" | ConvertTo-Json -Depth 3

# 4. Website integration (should work)
Invoke-RestMethod http://localhost:3010/api/natureos/mindex/health | ConvertTo-Json
```

## ✅ Expected Results

### Health Endpoint
```json
{
  "status": "ok",
  "db": "ok",  // ← This should be "ok" not "error"
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
  "etl_status": "idle"
}
```

### Observations Endpoint
```json
{
  "observations": [ ... array of observation records ... ],
  "total": 2491,
  "limit": 3,
  "offset": 0
}
```

## 🎯 After Fix - Website Features Work

Once database is connected, these pages will work:

✅ **http://localhost:3010/natureos/mindex**
- Overview dashboard
- Encyclopedia (species search)
- Data Pipeline (shows "online")
- All 12 sections functional

✅ **http://localhost:3010/natureos/mindex/explorer**
- Interactive map with observation pins
- Species filtering
- Spatial visualization

✅ **http://localhost:3010/mindex**
- Public portal
- Live stats
- Capability showcase

## 🔧 If Still Broken

### Check Docker Compose

```bash
ssh mycosoft@192.168.0.189
cd /home/mycosoft/mindex

# Check what's running
docker compose ps

# Check logs
docker compose logs mindex-api --tail 50
docker compose logs mindex-postgres --tail 50

# Full restart
docker compose down
docker compose up -d
```

### Check .env File

```bash
ssh mycosoft@192.168.0.189
cd /home/mycosoft/mindex
cat .env | grep -E "DATABASE_URL|DB_HOST|DB_PASSWORD"

# Should have:
# DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex
# MINDEX_DB_HOST=mindex-postgres
# MINDEX_DB_PASSWORD=mindex
```

### Check Database Itself

```bash
# Test direct postgres connection
docker exec -it mindex-postgres psql -U mindex -d mindex -c "SELECT version();"

# Check if tables exist
docker exec -it mindex-postgres psql -U mindex -d mindex -c "\dt"

# Count records
docker exec -it mindex-postgres psql -U mindex -d mindex -c "SELECT COUNT(*) FROM taxa;"
```

## 📞 Need Help?

If this doesn't work:
1. SSH password may be wrong - reset via Proxmox console
2. Database may need initialization - run migrations
3. Docker network may be misconfigured - check `docker network inspect mindex_mindex-network`
4. Port 8000 may be blocked by firewall - check `sudo ufw status`

---

**Total Time**: ~2 minutes
**Complexity**: Low (just restart containers)
**Impact**: HIGH (enables all MINDEX functionality)
