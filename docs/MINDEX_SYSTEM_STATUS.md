# MINDEX System Status & Documentation

**Last Updated:** January 12, 2026  
**Status:** ✅ Fully Operational

## Overview

MINDEX (Mycosoft Data Integrity Index) is the core taxonomic database and API service powering the Mycosoft platform. It provides comprehensive fungal species data, images, genetic information, and research publications.

## Current Database Statistics

| Metric | Count |
|--------|-------|
| **Total Taxa** | 19,387 |
| **Total Species** | 15,859 |
| **Taxa With Images** | 8,663 |
| iNaturalist Species | 4,357 |
| GBIF Species | 11,164 |
| FungiDB Records | 331 |
| Mushroom.World Species | 7 |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      MINDEX System                          │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │  MINDEX API │    │  MINDEX ETL │    │  PostgreSQL │     │
│  │  (FastAPI)  │◄──►│  Scheduler  │◄──►│  + PostGIS  │     │
│  │  Port 8000  │    │  (APScheduler)   │  Port 5432  │     │
│  └─────────────┘    └─────────────┘    └─────────────┘     │
│         │                  │                  │             │
│         │                  ▼                  │             │
│         │         ┌─────────────────┐         │             │
│         │         │ External APIs   │         │             │
│         │         │ • iNaturalist   │         │             │
│         │         │ • GBIF          │         │             │
│         │         │ • FungiDB       │         │             │
│         │         │ • MycoBank      │         │             │
│         │         │ • Mushroom.World│         │             │
│         │         └─────────────────┘         │             │
└─────────────────────────────────────────────────────────────┘
```

## Docker Services

All services run via `docker-compose.always-on.yml`:

| Service | Container Name | Port | Status |
|---------|---------------|------|--------|
| MINDEX API | `mycosoft-always-on-mindex-api-1` | 8000 | ✅ Healthy |
| MINDEX ETL | `mycosoft-always-on-mindex-etl-1` | - | ✅ Running |
| PostgreSQL | `mycosoft-always-on-mindex-postgres-1` | 5432 | ✅ Healthy |

## API Endpoints

### Taxa Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/mindex/taxa` | List taxa with filtering/sorting |
| GET | `/api/mindex/taxa/{uuid}` | Get single taxon by UUID |
| GET | `/api/mindex/taxa/search` | Search taxa by name |

#### Query Parameters for `/api/mindex/taxa`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 100 | Max results per page |
| `offset` | int | 0 | Pagination offset |
| `order_by` | string | canonical_name | Sort field |
| `order` | string | asc | Sort direction (asc/desc) |
| `rank` | string | - | Filter by rank (species, genus, etc.) |
| `source` | string | - | Filter by source (inat, gbif, etc.) |
| `prefix` | string | - | Filter by name prefix (A-Z browsing) |
| `q` | string | - | Search query |

### Image Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/mindex/images/stats` | Image coverage statistics |
| GET | `/api/mindex/images/missing` | List taxa without images |
| POST | `/api/mindex/images/backfill/start` | Start image backfill job |
| POST | `/api/mindex/images/backfill/{taxon_id}` | Backfill single taxon |
| POST | `/api/mindex/images/search/{taxon_id}` | Search images for taxon |

### Health Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Basic health check |
| GET | `/api/mindex/health` | Detailed health with DB status |

## ETL Scheduler

The ETL scheduler runs continuously, syncing data from external sources:

### Job Schedule

| Job | Schedule | Description |
|-----|----------|-------------|
| `inat_taxa` | Every 4 hours | Sync iNaturalist taxa |
| `inat_obs` | Every 6 hours | Sync iNaturalist observations |
| `gbif` | Every 12 hours | Sync GBIF species |
| `fungidb` | Every 24 hours | Sync FungiDB genomes |
| `mushroom_world` | Every 24 hours | Sync Mushroom.World species |
| `traits` | Every 12 hours | Backfill species traits |

### Configuration

Default `max_pages` per sync: **100** (configurable via `--max-pages` flag)

### Running Manual Syncs

```bash
# Run iNat taxa sync with 50 pages
docker exec mindex-etl python -m mindex_etl.jobs.sync_inat_taxa --max-pages 50

# Run GBIF complete sync
docker exec mindex-etl python -m mindex_etl.jobs.sync_gbif_complete --max-offset 5000

# Run image backfill
docker exec mindex-etl python -m mindex_etl.jobs.backfill_missing_images --limit 100
```

## Image Backfill Service

The multi-source image fetcher searches for species images from:

1. **iNaturalist** - Primary source, high-quality photos
2. **Wikipedia** - Encyclopedic images
3. **GBIF** - Biodiversity images
4. **Mushroom Observer** - Community photos
5. **Flickr** - Creative Commons images
6. **Bing Images** - Web scraping fallback
7. **Google Images** - Web scraping fallback

### Image Storage

Images are stored in taxon metadata as:
```json
{
  "default_photo": {
    "url": "https://...",
    "medium_url": "https://...",
    "source": "inat",
    "quality_score": 0.85
  }
}
```

## Database Schema

### Core Tables

```sql
-- core.taxon: Main taxonomic data
CREATE TABLE core.taxon (
    id UUID PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    scientific_name TEXT,
    common_name TEXT,
    rank TEXT,
    source TEXT,
    description TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

-- core.taxon_external_id: External ID mappings
CREATE TABLE core.taxon_external_id (
    id UUID PRIMARY KEY,
    taxon_id UUID REFERENCES core.taxon(id),
    source TEXT,
    external_id TEXT,
    metadata JSONB,
    UNIQUE(source, external_id)
);

-- bio.taxon_trait: Species traits
CREATE TABLE bio.taxon_trait (
    id UUID PRIMARY KEY,
    taxon_id UUID REFERENCES core.taxon(id),
    trait_name TEXT,
    value_text TEXT,
    value_numeric NUMERIC,
    value_unit TEXT,
    source TEXT,
    UNIQUE(taxon_id, trait_name)
);
```

## Configuration

### Environment Variables

```env
# Database
DATABASE_URL=postgresql://mindex:password@postgres:5432/mindex

# API
API_KEYS=local-dev-key,production-key
HTTP_TIMEOUT=30

# ETL
MAX_PAGES_DEFAULT=100
SYNC_INTERVAL_HOURS=4
```

## Troubleshooting

### Common Issues

1. **API returns 401 Unauthorized**
   - Ensure `X-API-Key` header is set
   - Check `API_KEYS` environment variable

2. **ETL job fails with connection error**
   - Check PostgreSQL container is running
   - Verify `DATABASE_URL` is correct

3. **Image backfill finds no images**
   - External APIs may be rate-limiting
   - Check network connectivity

### Viewing Logs

```bash
# API logs
docker logs mycosoft-always-on-mindex-api-1 --tail 100

# ETL logs
docker logs mycosoft-always-on-mindex-etl-1 --tail 100

# PostgreSQL logs
docker logs mycosoft-always-on-mindex-postgres-1 --tail 100
```

## Recent Changes (January 2026)

### ETL Fixes
- Fixed `max_pages` parameter compatibility across all sync jobs
- Fixed `bio.trait` → `bio.taxon_trait` table reference
- Fixed duplicate key constraint handling in `link_external_id`
- Added VEuPathDB fallback for FungiDB
- Increased default `max_pages` from 10 to 100

### API Fixes
- Fixed `authority` column error in `get_taxon` endpoint
- Fixed `API_KEYS` parsing for comma-separated strings
- Added image statistics and backfill endpoints
- Fixed asyncpg JSONB update syntax

### Image System
- Created `MultiImageFetcher` for multi-source image search
- Added image backfill service with 8+ sources
- Implemented synchronous backfill API endpoint

## Contributing

1. Clone the repository
2. Create a feature branch
3. Make changes and test locally
4. Submit a pull request

## License

Proprietary - Mycosoft Inc.
