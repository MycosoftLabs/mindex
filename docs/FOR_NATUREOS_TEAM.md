# MINDEX Integration - For NatureOS Development Team

## ✅ Integration Complete

MINDEX is now fully integrated into NatureOS's API gateway at `/api/natureos/mindex/*`.

## Quick Start

### Use These Endpoints in Your Widgets

```typescript
// Get database statistics (for stats widget)
const stats = await fetch('/api/natureos/mindex/stats');
// Returns: { total_taxa, total_observations, etl_status, ... }

// Search fungal species (for search widget)
const taxa = await fetch('/api/natureos/mindex/taxa?q=Amanita&limit=20');
// Returns: { data: Taxon[], pagination: {...} }

// Get observations (for map widget)
const obs = await fetch('/api/natureos/mindex/observations?limit=100');
// Returns: { data: Observation[], pagination: {...} }
```

### No API Keys Needed!

All authentication is handled server-side. Just call the endpoints directly from your React components.

## Available Endpoints

| Endpoint | Use Case |
|----------|----------|
| `/api/natureos/mindex/health` | Health check |
| `/api/natureos/mindex/stats` | **Stats widget** - Database counts, ETL status |
| `/api/natureos/mindex/taxa` | **Search widget** - Search species |
| `/api/natureos/mindex/taxa/{id}` | **Detail view** - Single species |
| `/api/natureos/mindex/observations` | **Map widget** - Observations with locations |

## Current Database

- **5,529** fungal species
- **2,491** observations (all with locations)
- **2,081** observations with images
- Continuously growing via ETL sync

## Test It Now

```bash
# Test stats endpoint
curl http://localhost:3000/api/natureos/mindex/stats

# Test taxa search
curl "http://localhost:3000/api/natureos/mindex/taxa?limit=5"

# View in API Explorer
# Open: http://localhost:3000/natureos/api
# Select "MINDEX" category
```

## Widget Development

See `NATUREOS_INTEGRATION_GUIDE.md` for:
- Complete widget component code
- API client setup
- Error handling patterns
- Real-time updates

## Architecture

```
Your Widget Component
    ↓ fetch('/api/natureos/mindex/stats')
NatureOS API Gateway (Next.js route handler)
    ↓ Proxies to MINDEX with auth
MINDEX API (localhost:8000/api/mindex/stats)
    ↓
PostgreSQL Database
```

**Key Point**: You never call `localhost:8000` directly - always use `/api/natureos/mindex/*`

## Documentation

- **Quick Start**: `README_NATUREOS_INTEGRATION.md`
- **Full Guide**: `NATUREOS_INTEGRATION_GUIDE.md` (with widget code)
- **API Details**: `NATUREOS_API_INTEGRATION.md`

---

**Everything is ready!** Just use `/api/natureos/mindex/*` endpoints in your widgets.
