# MINDEX → NatureOS Integration

**Status**: ✅ Integrated into NatureOS API Gateway  
**API Gateway**: `http://localhost:3000/api/natureos/mindex`  
**Direct API**: `http://localhost:8000/api/mindex` (requires `X-API-Key: your-api-key`)

## What You Need to Know

MINDEX is a fungal database API that's already running and populated with data. Your job is to create a NatureOS tab that displays this data using widget components.

## The Task

Create a new tab in NatureOS (`/mindex`) with 4 widgets:

1. **Stats Widget** - Shows database counts (taxa, observations)
2. **ETL Status Widget** - Shows if data sync is running
3. **Taxon Search Widget** - Search fungal species
4. **Observation Map Widget** - Map of observation locations

## Quick Start

1. **Read**: `NATUREOS_INTEGRATION_GUIDE.md` (complete guide)
2. **Reference**: `NATUREOS_INTEGRATION_QUICKSTART.md` (code snippets)
3. **Test**: `http://localhost:8000/docs` (API documentation)

## Key API Endpoints

All endpoints accessible through NatureOS API Gateway:

| Endpoint | Purpose | Returns |
|----------|---------|---------|
| `GET /api/natureos/mindex/health` | Health check | API status |
| `GET /api/natureos/mindex/stats` | Database stats | Counts, sources, ETL status |
| `GET /api/natureos/mindex/taxa?q={search}` | Search species | List of taxa |
| `GET /api/natureos/mindex/taxa/{id}` | Get single species | Taxon details |
| `GET /api/natureos/mindex/observations` | Get observations | List with locations/images |

## Example API Call

```typescript
// ✅ Use NatureOS API Gateway (no auth needed)
const response = await fetch('/api/natureos/mindex/stats');
const stats = await response.json();
// { total_taxa: 5529, total_observations: 2491, etl_status: "running", ... }
```

## Current Data

- **5,529** fungal species
- **2,491** observations (all with locations)
- **2,081** observations with images
- Continuously growing via ETL sync

## Files Created for You

✅ `mindex_api/routers/stats.py` - Stats endpoint (already added)  
✅ `docs/NATUREOS_INTEGRATION_GUIDE.md` - Full implementation guide  
✅ `docs/NATUREOS_INTEGRATION_QUICKSTART.md` - Quick reference  
✅ `docs/INTEGRATION_SUMMARY.md` - Executive summary

## What You Need to Create

- [ ] API client (`lib/integrations/mindex.ts`)
- [ ] Tab page (`app/(tabs)/mindex/page.tsx`)
- [ ] 4 widget components (see guide for code)
- [ ] Add to navigation

## Testing

```bash
# Test API is working
curl http://localhost:8000/api/mindex/health

# Test stats endpoint
curl http://localhost:8000/api/mindex/stats \
  -H "X-API-Key: your-api-key"
```

## Questions?

- **API Docs**: `http://localhost:8000/docs`
- **Full Guide**: See `NATUREOS_INTEGRATION_GUIDE.md`
- **Quick Reference**: See `NATUREOS_INTEGRATION_QUICKSTART.md`

---

**Start here**: Open `NATUREOS_INTEGRATION_GUIDE.md` for step-by-step instructions with complete code examples.
