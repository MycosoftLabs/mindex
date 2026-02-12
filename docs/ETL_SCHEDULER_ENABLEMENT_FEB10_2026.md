# ETL Scheduler Enablement Runbook - Feb 10, 2026

## Overview

This document describes how to enable and manage the MINDEX ETL scheduler, which automatically syncs data from external sources into the MINDEX database.

## Prerequisites

- SSH access to MINDEX VM (192.168.0.189)
- MINDEX Docker Compose running

## Enabling the ETL Scheduler

The ETL scheduler runs as a separate Docker container managed by a profile. To enable it:

```bash
# SSH to MINDEX VM
ssh mycosoft@192.168.0.189

# Navigate to MINDEX directory
cd /home/mycosoft/mindex

# Enable ETL scheduler (runs continuously in background)
docker compose --profile etl up -d

# Verify it's running
docker compose ps
```

You should see `mindex-etl` container running alongside `mindex-api` and `mindex-postgres`.

## Scheduled Jobs

The scheduler runs these jobs automatically:

| Job | Source | Interval | Description |
|-----|--------|----------|-------------|
| inat_taxa | iNaturalist | 24h | Fungal taxonomy (~26K species) |
| mycobank | MycoBank | Weekly | Taxa and synonyms (~545K species) |
| fungidb | FungiDB | Weekly | Genome metadata |
| traits | MW + Wikipedia | Weekly | Species traits |
| inat_obs | iNaturalist | 6h | Observations with images |
| gbif | GBIF | 24h | Occurrence records (~50K+) |
| hq_media | iNat/GBIF/Wiki | 12h | High quality images |
| publications | PubMed/GBIF | 48h | Research publications |
| chemspider | ChemSpider | Weekly | Chemical compounds |
| genetics | GenBank | Weekly | Genetic sequences |

## Manual Job Runs

To run jobs manually:

```bash
# Run all jobs once
docker compose exec mindex-api python -m mindex_etl.jobs.run_all --incremental

# Run specific jobs
docker compose exec mindex-api python -m mindex_etl.jobs.run_all --jobs inat_taxa mycobank

# Full sync (no page limits - takes hours)
docker compose exec mindex-api python -m mindex_etl.jobs.run_all --full

# List available jobs
docker compose exec mindex-api python -m mindex_etl.jobs.run_all --list-jobs
```

## Initial Data Load

For a fresh database, run the init job first:

```bash
# One-time initial data load (20 pages per source)
docker compose --profile init up etl-init
```

## Viewing Logs

```bash
# ETL scheduler logs
docker compose logs -f mindex-etl

# Last 100 lines
docker compose logs --tail=100 mindex-etl
```

## Stopping the Scheduler

```bash
# Stop ETL scheduler only
docker compose --profile etl stop mindex-etl

# Stop all MINDEX services
docker compose --profile etl down
```

## Troubleshooting

### Scheduler not running jobs
Check if the container is healthy:
```bash
docker compose ps
docker compose logs mindex-etl --tail=50
```

### Database connection issues
Verify PostgreSQL is running:
```bash
docker compose exec db pg_isready -U mindex
```

### Job failures
Check individual job logs and retry manually:
```bash
docker compose exec mindex-api python -m mindex_etl.jobs.run_all --jobs inat_taxa 2>&1 | head -100
```

## Related Documentation

- `mindex_etl/scheduler.py` - Scheduler implementation
- `mindex_etl/jobs/run_all.py` - Job registry
- `docker-compose.yml` - Container configuration
