# MINDEX Integration into NatureOS API Gateway

## Overview

MINDEX is now integrated into NatureOS's API gateway structure at `/api/natureos/mindex/`. This provides a unified API interface where all NatureOS services are accessible through a single gateway.

## Architecture

```
NatureOS Frontend (localhost:3000)
    ↓
NatureOS API Gateway (/api/natureos/mindex/*)
    ↓ (BFF Proxy)
MINDEX API (localhost:8000/api/mindex/*)
    ↓
PostgreSQL Database (localhost:5434)
```

## Available Endpoints

All MINDEX endpoints are now accessible through NatureOS's API gateway:

### Base URL
```
http://localhost:3000/api/natureos/mindex
```

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/natureos/mindex/health` | GET | Health check and database status |
| `/api/natureos/mindex/stats` | GET | Database statistics and ETL sync status |
| `/api/natureos/mindex/taxa` | GET | Search/list fungal species |
| `/api/natureos/mindex/taxa/{id}` | GET | Get single species details |
| `/api/natureos/mindex/observations` | GET | Get observations with locations/images |

## Implementation Details

### Proxy Routes Created

All routes are Next.js API routes that act as BFF (Backend for Frontend) proxies:

- `app/api/natureos/mindex/health/route.ts` - Health check proxy
- `app/api/natureos/mindex/stats/route.ts` - Statistics proxy
- `app/api/natureos/mindex/taxa/route.ts` - Taxa list/search proxy
- `app/api/natureos/mindex/taxa/[id]/route.ts` - Single taxon proxy
- `app/api/natureos/mindex/observations/route.ts` - Observations proxy

### How It Works

1. **Client Request**: Frontend calls `/api/natureos/mindex/stats`
2. **Next.js Route Handler**: Receives request, adds `X-API-Key` header
3. **Proxy to MINDEX**: Forwards request to `http://localhost:8000/api/mindex/stats`
4. **Response**: Returns MINDEX response to client

### Benefits

- ✅ **Unified API**: All services accessible through `/api/natureos/*`
- ✅ **Security**: API keys never exposed to client
- ✅ **Caching**: Next.js can cache responses
- ✅ **Error Handling**: Centralized error handling
- ✅ **Monitoring**: All requests go through NatureOS gateway

## Usage Examples

### Get Database Statistics

```typescript
// Frontend code
const response = await fetch('/api/natureos/mindex/stats');
const stats = await response.json();
// Returns: { total_taxa, total_observations, etl_status, ... }
```

### Search Fungal Species

```typescript
const response = await fetch('/api/natureos/mindex/taxa?q=Amanita&limit=20');
const data = await response.json();
// Returns: { data: Taxon[], pagination: {...} }
```

### Get Observations

```typescript
const response = await fetch('/api/natureos/mindex/observations?limit=100');
const data = await response.json();
// Returns: { data: Observation[], pagination: {...} }
```

## Environment Variables

Add to NatureOS `.env.local`:

```bash
# MINDEX API Configuration
MINDEX_API_BASE_URL=http://localhost:8000/api/mindex
MINDEX_API_KEY=your-api-key
```

## API Explorer

The NatureOS API Explorer at `/natureos/api` now includes all MINDEX endpoints:

- Navigate to `http://localhost:3000/natureos/api`
- Select "MINDEX" category
- Test endpoints directly in the browser
- View request/response examples

## Widget Integration

For NatureOS widgets, use the NatureOS API gateway endpoints:

```typescript
// ✅ Use NatureOS gateway
const stats = await fetch('/api/natureos/mindex/stats');

// ❌ Don't call MINDEX directly from client
// const stats = await fetch('http://localhost:8000/api/mindex/stats');
```

## Current Status

- ✅ MINDEX API running on `localhost:8000`
- ✅ Proxy routes created under `/api/natureos/mindex/`
- ✅ API Explorer updated with MINDEX endpoints
- ✅ All endpoints forward to MINDEX correctly
- ✅ Error handling and caching configured

## Testing

```bash
# Test through NatureOS gateway
curl http://localhost:3000/api/natureos/mindex/health

# Test stats endpoint
curl http://localhost:3000/api/natureos/mindex/stats

# Test taxa search
curl "http://localhost:3000/api/natureos/mindex/taxa?q=Amanita&limit=5"
```

## Next Steps for Widget Development

1. Use `/api/natureos/mindex/*` endpoints in widgets
2. No need to configure API keys in frontend
3. All requests automatically cached by Next.js
4. Errors handled consistently

## Notes

- MINDEX must be running on `localhost:8000` for proxies to work
- If MINDEX is down, proxies return 503 with error message
- All responses are cached (10s-5min depending on endpoint)
- API keys are handled server-side only
