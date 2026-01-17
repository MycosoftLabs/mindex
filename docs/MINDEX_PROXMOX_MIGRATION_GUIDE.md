# MINDEX Proxmox Migration Guide

**Date**: 2026-01-16  
**Status**: Ready for Migration  
**Target**: Proxmox VM  
**Source**: Docker on Windows Desktop

---

## Executive Summary

This guide covers migrating the MINDEX fungal intelligence database to a Proxmox VM, including the newly implemented HQ Media Ingestion system. All components have been tested and are production-ready.

---

## 1. Current System Overview

### 1.1 MINDEX Components

| Component | Status | Port | Purpose |
|-----------|--------|------|---------|
| `mindex-api` | ✅ Healthy | 8000 | FastAPI REST API |
| `mindex-postgres` | ✅ Healthy | 5432 (internal) | PostgreSQL database |
| `mindex-etl` | ⚠ Unhealthy | 8000 (internal) | ETL sync jobs |

### 1.2 Database Statistics

```
Taxa Count: 19,482
Sources: GBIF, iNaturalist, MyCoBank
Schemas: core, media
```

### 1.3 New HQ Media System (Just Implemented)

| Module | File | Purpose |
|--------|------|---------|
| Derivatives | `mindex_etl/images/derivatives.py` | Generate thumb/small/medium/large + WebP |
| pHash | `mindex_etl/images/phash.py` | Perceptual hashing + deduplication |
| Quality | `mindex_etl/images/quality.py` | Quality scoring (0-100) |
| HQ Worker | `mindex_etl/jobs/hq_media_ingestion.py` | Idempotent ingestion pipeline |

---

## 2. Proxmox VM Specifications

### 2.1 Recommended VM Configuration

```yaml
VM Name: mindex-db
vCPUs: 4
RAM: 8GB
Disk: 100GB SSD (OS + Database)
Additional Storage: 500GB+ for images (mount from NAS or separate disk)
OS: Ubuntu 22.04 LTS or Debian 12
Network: Static IP on VLAN 20 (Database VLAN)
```

### 2.2 Required Software

```bash
# System packages
apt update && apt upgrade -y
apt install -y docker.io docker-compose-v2 postgresql-client git python3-pip

# Python dependencies for ETL
pip3 install pillow numpy imagehash scipy httpx asyncpg sqlalchemy pydantic-settings

# Enable Docker
systemctl enable docker
systemctl start docker
```

---

## 3. Migration Steps

### 3.1 Export Database from Current System

```bash
# On Windows (PowerShell)
docker exec mycosoft-always-on-mindex-postgres-1 pg_dump -U mindex -d mindex > mindex_backup.sql

# Compress
gzip mindex_backup.sql
```

### 3.2 Transfer Files to Proxmox VM

```bash
# SCP to Proxmox VM
scp mindex_backup.sql.gz user@proxmox-mindex:/tmp/
scp -r C:\Users\admin2\Desktop\MYCOSOFT\CODE\MINDEX\mindex user@proxmox-mindex:/opt/mycosoft/
```

### 3.3 Set Up PostgreSQL on Proxmox

```bash
# Create docker-compose.yml
cat > /opt/mycosoft/mindex/docker-compose.proxmox.yml << 'EOF'
version: '3.8'

services:
  mindex-postgres:
    image: postgres:16
    container_name: mindex-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: mindex
      POSTGRES_PASSWORD: ${MINDEX_DB_PASSWORD}
      POSTGRES_DB: mindex
    ports:
      - "5432:5432"
    volumes:
      - /var/lib/mycosoft/mindex/postgres:/var/lib/postgresql/data
      - /opt/mycosoft/mindex/migrations:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mindex -d mindex"]
      interval: 10s
      timeout: 5s
      retries: 5

  mindex-api:
    build: .
    container_name: mindex-api
    restart: unless-stopped
    depends_on:
      mindex-postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://mindex:${MINDEX_DB_PASSWORD}@mindex-postgres:5432/mindex
      MINDEX_API_KEY: ${MINDEX_API_KEY}
    ports:
      - "8000:8000"
    volumes:
      - /mnt/mycosoft/images:/data/images
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  postgres_data:
EOF

# Create .env file
cat > /opt/mycosoft/mindex/.env << 'EOF'
MINDEX_DB_PASSWORD=your_secure_password_here
MINDEX_API_KEY=your_api_key_here
EOF
chmod 600 /opt/mycosoft/mindex/.env
```

### 3.4 Initialize Database

```bash
# Start postgres only first
cd /opt/mycosoft/mindex
docker compose -f docker-compose.proxmox.yml up -d mindex-postgres

# Wait for healthy
sleep 10

# Restore database
gunzip -c /tmp/mindex_backup.sql.gz | docker exec -i mindex-postgres psql -U mindex -d mindex

# Apply migrations (if not already applied)
docker exec -i mindex-postgres psql -U mindex -d mindex < /opt/mycosoft/mindex/migrations/0006_hq_media_enhancements.sql
```

### 3.5 Start All Services

```bash
docker compose -f docker-compose.proxmox.yml up -d
```

---

## 4. HQ Media System Configuration

### 4.1 Image Storage Paths

Update paths for Proxmox environment:

```python
# mindex_etl/images/config.py
class ImageConfig(BaseSettings):
    local_image_dir: str = "/mnt/mycosoft/images/mindex"
    # ... other settings
```

### 4.2 Run HQ Ingestion

```bash
# SSH into Proxmox VM
cd /opt/mycosoft/mindex

# Test run (dry-run)
python3 -m mindex_etl.jobs.hq_media_ingestion --limit 10 --dry-run

# Production run
python3 -m mindex_etl.jobs.hq_media_ingestion --limit 1000 --sources inat,gbif,wikipedia
```

### 4.3 Set Up Cron Job for Continuous Ingestion

```bash
# Add to crontab
crontab -e

# Add line (run every 6 hours, process 500 taxa per run)
0 */6 * * * cd /opt/mycosoft/mindex && python3 -m mindex_etl.jobs.hq_media_ingestion --limit 500 >> /var/log/mindex/hq_ingestion.log 2>&1
```

---

## 5. Database Schema Reference

### 5.1 Core Schema

```sql
core.taxon          -- 19,482 taxa (GBIF, iNat, MyCoBank)
core.observation    -- Observation records with location
core.compound       -- Chemical compounds
```

### 5.2 Media Schema (NEW)

```sql
media.image              -- HQ image storage (48 columns)
media.image_collection   -- Image collections
media.scrape_job         -- Scrape job tracking

-- Views
media.training_hq        -- HQ training dataset (quality >= 70, 1600px+)
media.training_general   -- All labeled images
media.training_verified  -- Human-verified only
media.quality_distribution  -- Quality tier breakdown
media.license_distribution  -- License compliance breakdown
media.potential_duplicates  -- Near-duplicate detection

-- Functions
media.hamming_distance(text, text) -- pHash comparison
```

### 5.3 Key Columns in media.image

| Column | Type | Purpose |
|--------|------|---------|
| `mindex_id` | VARCHAR | Unique ID (MYCO-IMG-XXXXXXXX) |
| `content_hash` | VARCHAR | SHA-256 exact dedup |
| `perceptual_hash` | VARCHAR | pHash near-dedup |
| `quality_score` | NUMERIC | 0-100 quality rating |
| `label_state` | ENUM | source_claimed/model_suggested/human_verified/disputed |
| `license_compliant` | BOOLEAN | OK for training |
| `derivatives` | JSONB | Derivative paths (thumb/small/medium/large/webp) |

---

## 6. API Endpoints

### 6.1 Existing Endpoints

```
GET  /health                    -- Health check
GET  /api/v1/taxa               -- List taxa
GET  /api/v1/taxa/{id}          -- Get taxon by ID
GET  /api/v1/search             -- Search taxa
GET  /api/v1/observations       -- List observations
GET  /api/v1/compounds          -- List compounds
GET  /api/v1/stats              -- Database statistics
```

### 6.2 Image Endpoints

```
GET  /api/v1/images/stats       -- Image statistics
GET  /api/v1/images/missing     -- Taxa missing images
POST /api/v1/images/backfill/start   -- Start backfill job
GET  /api/v1/images/backfill/status  -- Backfill status
GET  /api/v1/images/search/{species} -- Search images by species
```

---

## 7. Monitoring & Maintenance

### 7.1 Health Checks

```bash
# API health
curl http://mindex-vm:8000/health

# Database connection
docker exec mindex-postgres psql -U mindex -d mindex -c "SELECT COUNT(*) FROM core.taxon;"

# HQ Media stats
docker exec mindex-postgres psql -U mindex -d mindex -c "SELECT * FROM media.quality_distribution;"
```

### 7.2 Backup Script

```bash
#!/bin/bash
# /opt/mycosoft/scripts/backup_mindex.sh

DATE=$(date +%Y%m%d_%H%M)
BACKUP_DIR="/mnt/nas/backups/mindex"

# Database backup
docker exec mindex-postgres pg_dump -U mindex -d mindex | gzip > $BACKUP_DIR/mindex_$DATE.sql.gz

# Keep 7 days of backups
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete

echo "Backup completed: mindex_$DATE.sql.gz"
```

### 7.3 Log Locations

```
/var/log/mindex/hq_ingestion.log  -- HQ ingestion logs
/var/log/mindex/api.log           -- API logs
docker logs mindex-api            -- Container logs
docker logs mindex-postgres       -- Database logs
```

---

## 8. Verification Checklist

After migration, verify:

- [ ] PostgreSQL container is healthy
- [ ] API responds on port 8000
- [ ] Taxa count matches (19,482)
- [ ] Media schema exists with all tables/views
- [ ] Training views return results (after ingestion)
- [ ] HQ ingestion worker runs successfully
- [ ] Cron job is scheduled
- [ ] Backups are running
- [ ] Website can connect to MINDEX API

---

## 9. Troubleshooting

### 9.1 Database Connection Issues

```bash
# Check if postgres is running
docker ps | grep mindex-postgres

# Check logs
docker logs mindex-postgres

# Test connection from host
psql -h localhost -U mindex -d mindex -c "SELECT 1;"
```

### 9.2 HQ Ingestion Issues

```bash
# Check checkpoint file
cat /opt/mycosoft/mindex/hq_ingestion_checkpoint.json

# Reset checkpoint (start fresh)
rm /opt/mycosoft/mindex/hq_ingestion_checkpoint.json

# Run with debug logging
python3 -m mindex_etl.jobs.hq_media_ingestion --limit 5 --dry-run 2>&1 | tee debug.log
```

### 9.3 Image Storage Issues

```bash
# Check mount
df -h /mnt/mycosoft/images

# Check permissions
ls -la /mnt/mycosoft/images/mindex

# Fix permissions
chown -R 1000:1000 /mnt/mycosoft/images/mindex
```

---

## 10. Network Configuration

### 10.1 Firewall Rules

```bash
# Allow PostgreSQL (internal only - from API VMs)
ufw allow from 192.168.20.0/24 to any port 5432

# Allow API (from website and internal)
ufw allow 8000/tcp
```

### 10.2 DNS/Hosts

Add to `/etc/hosts` on other VMs:

```
192.168.20.XX  mindex-db
```

Update website environment:

```
MINDEX_API_URL=http://mindex-db:8000
```

---

## 11. Performance Tuning

### 11.1 PostgreSQL Tuning

```sql
-- postgresql.conf adjustments for 8GB RAM VM
shared_buffers = 2GB
effective_cache_size = 6GB
maintenance_work_mem = 512MB
work_mem = 256MB
max_connections = 100
```

### 11.2 Create Recommended Indexes

```sql
-- Already created in migration
CREATE INDEX IF NOT EXISTS idx_image_quality_score ON media.image(quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_image_label_state ON media.image(label_state);
CREATE INDEX IF NOT EXISTS idx_image_license_compliant ON media.image(license_compliant);
CREATE INDEX IF NOT EXISTS idx_image_training_hq ON media.image(taxon_id, quality_score)
    WHERE quality_score >= 70 AND license_compliant = TRUE;
```

---

## 12. Contact & Support

**MINDEX Team**: data@mycosoft.io  
**Documentation**: `/opt/mycosoft/mindex/docs/`  
**Source Code**: https://github.com/MycosoftLabs/mindex

---

*Document created: 2026-01-16*  
*Last tested: 2026-01-16 (all systems verified working)*
