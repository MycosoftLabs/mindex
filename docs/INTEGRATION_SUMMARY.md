# MINDEX → NatureOS Integration Summary

## For the NatureOS Development Team

This document provides a concise explanation of how to integrate MINDEX into NatureOS as a tabbed application with widget components.

## What is MINDEX?

MINDEX (Mycosoft Index) is a comprehensive fungal database API that:
- Contains **5,500+ fungal species** (taxa) from iNaturalist, GBIF, MycoBank
- Has **2,500+ observations** with geolocation and images
- Continuously syncs data from multiple sources via ETL jobs
- Provides RESTful API for querying fungal data
- Runs at `localhost:8000/api/mindex`

## Integration Goal

Add MINDEX as a new tab in NatureOS with:
1. **Database Statistics Widget** - Shows total taxa, observations, data sources
2. **ETL Sync Status Widget** - Shows if data is being pulled (running/idle)
3. **Taxon Search Widget** - Search and browse fungal species
4. **Observation Map Widget** - Visualize observation locations on a map

## Quick Integration Steps

### 1. API Client (5 minutes)
Create `lib/integrations/mindex.ts` with HTTP client pointing to `http://localhost:8000/api/mindex` using `X-API-Key: local-dev-key` header.

### 2. Stats Endpoint (Already Added ✅)
The `/api/mindex/stats` endpoint is now available and returns:
- Database counts (taxa, observations)
- Data breakdown by source
- ETL sync status (`running` | `idle` | `unknown`)

### 3. Tab Page (15 minutes)
Create `app/(tabs)/mindex/page.tsx` that:
- Fetches stats on load
- Displays 4 widget components
- Refreshes stats every 30 seconds

### 4. Widget Components (30-45 minutes each)
- **StatsWidget**: Display numbers from `/stats` endpoint
- **ETLSyncWidget**: Show sync status with indicator
- **TaxonSearchWidget**: Search using `/taxa?q={query}`
- **ObservationMapWidget**: Map using `/observations` with GeoJSON locations

### 5. Navigation (2 minutes)
Add MINDEX to your tabs/navigation config.

## API Endpoints You'll Use

```typescript
// Health check
GET /api/mindex/health

// Get statistics (for widgets)
GET /api/mindex/stats
// Returns: { total_taxa, total_observations, etl_status, ... }

// Search taxa
GET /api/mindex/taxa?q={search}&limit=20
// Returns: { data: Taxon[], pagination: {...} }

// Get observations (for map)
GET /api/mindex/observations?limit=100
// Returns: { data: Observation[], pagination: {...} }
// Each observation has: location (GeoJSON), media (images), taxon_id
```

## Data Structures

### Taxon
```typescript
{
  id: string,
  canonical_name: "Amanita muscaria",
  common_name: "Fly Agaric",
  rank: "species",
  source: "inat",
  metadata: { ... }
}
```

### Observation
```typescript
{
  id: string,
  taxon_id: string,
  observed_at: "2025-12-29T00:00:00Z",
  location: {
    type: "Point",
    coordinates: [longitude, latitude]
  },
  media: [{ url: "...", attribution: "..." }],
  observer: "username"
}
```

### Stats Response
```typescript
{
  total_taxa: 5529,
  total_observations: 2491,
  taxa_by_source: { inat: 5020, gbif: 509 },
  observations_by_source: { inat: 1991, gbif: 500 },
  observations_with_location: 2491,
  observations_with_images: 2081,
  etl_status: "running" | "idle" | "unknown"
}
```

## Environment Variables

Add to NatureOS `.env.local`:
```bash
MINDEX_API_BASE_URL=http://localhost:8000/api/mindex
MINDEX_API_KEY=local-dev-key
```

## Testing

1. **Verify MINDEX is running**:
   ```bash
   curl http://localhost:8000/api/mindex/health
   ```

2. **Test stats endpoint**:
   ```bash
   curl http://localhost:8000/api/mindex/stats \
     -H "X-API-Key: local-dev-key"
   ```

3. **View API docs**:
   ```
   http://localhost:8000/docs
   ```

## Current Database Status

- **5,529** fungal taxa (species)
- **2,491** observations with locations
- **2,081** observations with images
- **713** unique taxa with observations
- Data sources: iNaturalist (primary), GBIF
- ETL sync: Running continuously in background

## Files to Reference

1. **Full Integration Guide**: `docs/NATUREOS_INTEGRATION_GUIDE.md`
   - Complete implementation details
   - Full code examples
   - Error handling patterns
   - Advanced features

2. **Quick Start**: `docs/NATUREOS_INTEGRATION_QUICKSTART.md`
   - TL;DR version
   - Key endpoints
   - Code snippets

3. **API Documentation**: `http://localhost:8000/docs`
   - Interactive Swagger UI
   - Test endpoints directly
   - See request/response schemas

## Support

- MINDEX API Health: `http://localhost:8000/api/mindex/health`
- Check ETL Status: `docker ps --filter "name=mindex-full-sync"`
- View ETL Logs: `docker logs mindex-full-sync-v2`

## Notes

- All MINDEX routes are under `/api/mindex` prefix
- Authentication via `X-API-Key` header (not Bearer token)
- CORS is configured for `localhost:3000` (NatureOS)
- Database is continuously updated - data grows over time
- Rate limits are handled automatically by the API

## Next Steps After Integration

1. Add pagination to search results
2. Add filters (by source, rank, date range)
3. Add detail views for taxa and observations
4. Add data visualization charts
5. Add export functionality
6. Add favorites/bookmarks

---

**Ready to integrate?** Start with the Quick Start guide and reference the full guide for detailed implementation.
