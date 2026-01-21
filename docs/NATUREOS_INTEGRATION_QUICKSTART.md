# MINDEX → NatureOS Integration Quick Start

## TL;DR

MINDEX is a fungal database API running at `localhost:8000/api/mindex`. Integrate it into NatureOS as a new tab with widgets showing:
- Database statistics (taxa count, observations)
- ETL sync status (is data being pulled?)
- Taxon search
- Observation map

## API Quick Reference

**Base URL**: `http://localhost:8000/api/mindex`  
**Auth Header**: `X-API-Key: your-api-key`

### Key Endpoints

```typescript
GET /health                    // API health check
GET /taxa?q={search}          // Search fungal species
GET /taxa/{id}                // Get single species
GET /observations?limit=100   // Get observations with locations/images
GET /stats                     // Database stats + ETL status
```

## Integration Checklist

- [ ] Create `lib/integrations/mindex.ts` API client
- [ ] Add `/stats` endpoint to MINDEX API (see guide)
- [ ] Create `app/(tabs)/mindex/page.tsx` tab page
- [ ] Build 4 widget components:
  - [ ] `MINDEXStatsWidget` - Shows total taxa/observations
  - [ ] `ETLSyncStatusWidget` - Shows if data sync is running
  - [ ] `TaxonSearchWidget` - Search fungal species
  - [ ] `ObservationMapWidget` - Map of observations
- [ ] Add MINDEX to navigation/tabs
- [ ] Add env vars: `MINDEX_API_BASE_URL`, `MINDEX_API_KEY`

## Code Snippets

### API Client Setup
```typescript
const mindexClient = createHttpClient({
  baseUrl: 'http://localhost:8000/api/mindex',
  headers: { 'X-API-Key': 'your-api-key' },
});
```

### Get Stats
```typescript
const stats = await mindexApi.getStats();
// Returns: { total_taxa, total_observations, taxa_by_source, ... }
```

### Search Taxa
```typescript
const results = await mindexApi.getTaxa({ q: 'Amanita', limit: 20 });
// Returns: { data: Taxon[], pagination: {...} }
```

### Get Observations
```typescript
const obs = await mindexApi.getObservations({ limit: 100 });
// Returns: { data: Observation[], pagination: {...} }
// Each observation has: location (GeoJSON), media (images), taxon_id
```

## Current Database Status

As of now, MINDEX contains:
- **5,529** fungal taxa (species)
- **2,491** observations with locations
- **2,081** observations with images
- Data sources: iNaturalist, GBIF
- ETL sync running continuously in background

## Widget Data Structure

### Stats Response
```typescript
{
  total_taxa: 5529,
  total_observations: 2491,
  taxa_by_source: { inat: 5020, gbif: 509 },
  observations_by_source: { inat: 1991, gbif: 500 },
  observations_with_location: 2491,
  observations_with_images: 2081,
  taxa_with_observations: 713,
  observation_date_range: { earliest: "...", latest: "..." },
  etl_status: "running" | "idle" | "unknown"
}
```

### Taxon Object
```typescript
{
  id: "uuid",
  canonical_name: "Amanita muscaria",
  common_name: "Fly Agaric",
  rank: "species",
  source: "inat",
  metadata: { inat_id: 123, ... }
}
```

### Observation Object
```typescript
{
  id: "uuid",
  taxon_id: "uuid",
  observed_at: "2025-12-29T00:00:00Z",
  location: { type: "Point", coordinates: [lng, lat] },
  media: [{ url: "...", attribution: "..." }],
  observer: "username",
  notes: "..."
}
```

## Testing

```bash
# 1. Check MINDEX is running
curl http://localhost:8000/api/mindex/health

# 2. Test in browser
http://localhost:8000/docs  # Swagger UI

# 3. Check ETL sync status
docker ps --filter "name=mindex-full-sync"
```

## Files to Create/Modify

**NatureOS Website:**
- `lib/integrations/mindex.ts` - API client
- `app/(tabs)/mindex/page.tsx` - Main tab page
- `components/mindex/MINDEXStatsWidget.tsx`
- `components/mindex/ETLSyncStatusWidget.tsx`
- `components/mindex/TaxonSearchWidget.tsx`
- `components/mindex/ObservationMapWidget.tsx`

**MINDEX API (if needed):**
- `mindex_api/routers/stats.py` - Add stats endpoint
- Register in `mindex_api/main.py`

## Full Documentation

See `docs/NATUREOS_INTEGRATION_GUIDE.md` for complete implementation details.
