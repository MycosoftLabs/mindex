# MINDEX Changelog

All notable changes to the MINDEX project.

## [2.1.0] - 2026-01-12

### Added
- **Image Backfill Service** - Multi-source image fetcher with support for:
  - iNaturalist
  - Wikipedia
  - GBIF
  - Mushroom Observer
  - Flickr
  - Bing Images
  - Google Images
- **Image API Endpoints**:
  - `GET /api/mindex/images/stats` - Image coverage statistics
  - `GET /api/mindex/images/missing` - List taxa without images
  - `POST /api/mindex/images/backfill/start` - Batch image backfill
  - `POST /api/mindex/images/backfill/{taxon_id}` - Single taxon backfill
- **New ETL Jobs**:
  - `backfill_missing_images.py` - Background image backfill job
  - `sync_fusarium_taxa.py` - Fusarium database sync
  - `sync_mushroom_world_taxa.py` - Mushroom.World sync
  - `sync_theyeasts_taxa.py` - TheYeasts.org sync
  - `sync_gbif_complete.py` - Complete GBIF species sync
- **Checkpoint System** - Resume ETL jobs from last successful point
- **Stats API** - Database statistics endpoint

### Changed
- Increased default `max_pages` from 10 to 100 for larger syncs
- Updated scheduler to run jobs more frequently
- Improved error handling in all ETL jobs
- Changed `bio.trait` references to `bio.taxon_trait`

### Fixed
- **API Key Parsing** - Now accepts comma-separated strings in addition to JSON lists
- **Taxon Endpoint** - Fixed `authority` column error by querying `core.taxon` directly
- **External ID Linking** - Fixed duplicate key constraint with `ON CONFLICT DO NOTHING`
- **FungiDB Source** - Added 404 handling with VEuPathDB fallback
- **Mushroom.World Source** - Fixed syntax error in API iterator
- **JSONB Updates** - Fixed asyncpg syntax for `jsonb_set` operations

### Database
- Added `migrations/0005_images.sql` for image storage schema
- Added vector embedding support for image similarity search

## [2.0.0] - 2026-01-10

### Added
- NatureOS integration
- PostgreSQL + PostGIS support
- Full-text search capabilities
- Observation geospatial indexing

### Changed
- Migrated from SQLite to PostgreSQL
- Restructured API routes for consistency

## [1.0.0] - 2025-12-01

### Added
- Initial MINDEX API release
- iNaturalist taxa sync
- GBIF species import
- Basic search functionality
