# MINDEX Integration Guide for NatureOS

## Overview

This guide explains how to integrate MINDEX (Mycosoft Index) into NatureOS as a tabbed application with widget components, real-time data visualization, and API integration. MINDEX provides comprehensive fungal taxonomy, observation, and genomic data that can be displayed in NatureOS.

## Architecture Overview

```
NatureOS (localhost:3000)
    ↓
MINDEX API (localhost:8000/api/mindex)
    ↓
PostgreSQL Database (localhost:5434)
    ↓
ETL Jobs (Continuous data sync from iNaturalist, GBIF, MycoBank, etc.)
```

## API Endpoints

### Base URL (Two Options)

**Option 1: Through NatureOS API Gateway (Recommended)**
```
http://localhost:3000/api/natureos/mindex
```
- ✅ No API key needed (handled server-side)
- ✅ Cached responses
- ✅ Unified with other NatureOS APIs
- ✅ Better error handling

**Option 2: Direct MINDEX API**
```
http://localhost:8000/api/mindex
```
- Requires `X-API-Key: your-api-key` header
- Direct access (bypasses gateway)

### Authentication

**For NatureOS Gateway** (Option 1):
- No authentication needed in frontend code
- API keys handled server-side automatically

**For Direct API** (Option 2):
All requests require the `X-API-Key` header:
```typescript
headers: {
  "X-API-Key": "your-api-key"  // Example key
}
```

### Available Endpoints

#### 1. Health Check
```typescript
GET /api/mindex/health
Response: {
  status: "ok",
  db: "ok",
  timestamp: "2025-12-29T01:04:01.729657Z",
  service: "mindex",
  version: "0.1.0",
  git_sha: string | null
}
```

#### 2. List Taxa (Fungal Species)
```typescript
GET /api/mindex/taxa?q={search}&rank={rank}&limit={limit}&offset={offset}
Response: {
  data: Array<{
    id: string (UUID),
    canonical_name: string,
    rank: string,
    common_name: string | null,
    authority: string | null,
    description: string | null,
    source: string,
    metadata: object,
    created_at: string (ISO 8601),
    updated_at: string (ISO 8601)
  }>,
  pagination: {
    limit: number,
    offset: number,
    total: number
  }
}
```

#### 3. Get Single Taxon
```typescript
GET /api/mindex/taxa/{taxon_id}
Response: TaxonResponse (same structure as above, single object)
```

#### 4. List Observations
```typescript
GET /api/mindex/observations?taxon_id={uuid}&start={date}&end={date}&bbox={min_lon,min_lat,max_lon,max_lat}&limit={limit}&offset={offset}
Response: {
  data: Array<{
    id: string (UUID),
    taxon_id: string (UUID),
    source: string,
    source_id: string,
    observer: string | null,
    observed_at: string (ISO 8601),
    accuracy_m: number | null,
    media: Array<{
      url: string,
      attribution: string | null,
      license_code: string | null
    }>,
    notes: string | null,
    metadata: object,
    location: {
      type: "Point",
      coordinates: [longitude, latitude],
      properties: null
    } | null
  }>,
  pagination: {
    limit: number,
    offset: number,
    total: number
  }
}
```

#### 5. Database Statistics (Custom Endpoint - See Below)
```typescript
GET /api/mindex/stats
Response: {
  total_taxa: number,
  total_observations: number,
  taxa_by_source: { [source: string]: number },
  observations_by_source: { [source: string]: number },
  observations_with_location: number,
  observations_with_images: number,
  taxa_with_observations: number,
  observation_date_range: {
    earliest: string | null,
    latest: string | null
  }
}
```

## Integration Steps

### Step 1: Create MINDEX API Client

Create or update `lib/integrations/mindex.ts` in the NatureOS website:

```typescript
// lib/integrations/mindex.ts
// Use NatureOS API Gateway - no API keys needed!

export interface Taxon {
  id: string;
  canonical_name: string;
  rank: string;
  common_name: string | null;
  authority: string | null;
  description: string | null;
  source: string;
  metadata: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface Observation {
  id: string;
  taxon_id: string;
  source: string;
  source_id: string;
  observer: string | null;
  observed_at: string;
  accuracy_m: number | null;
  media: Array<{
    url: string;
    attribution: string | null;
    license_code: string | null;
  }>;
  notes: string | null;
  metadata: Record<string, any>;
  location: {
    type: 'Point';
    coordinates: [number, number]; // [lng, lat]
    properties: null;
  } | null;
}

export interface PaginatedResponse<T> {
  data: T[];
  pagination: {
    limit: number;
    offset: number;
    total: number;
  };
}

export interface MINDEXStats {
  total_taxa: number;
  total_observations: number;
  taxa_by_source: Record<string, number>;
  observations_by_source: Record<string, number>;
  observations_with_location: number;
  observations_with_images: number;
  taxa_with_observations: number;
  observation_date_range: {
    earliest: string | null;
    latest: string | null;
  };
  etl_status: 'running' | 'idle' | 'unknown';
}

// Use NatureOS API Gateway - proxies handle authentication
const API_BASE = '/api/natureos/mindex';

export const mindexApi = {
  // Health check
  async getHealth() {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
    return res.json() as Promise<{
      status: string;
      db: string;
      timestamp: string;
      service: string;
      version: string;
      git_sha: string | null;
    }>;
  },

  // Taxa
  async getTaxa(params?: {
    q?: string;
    rank?: string;
    limit?: number;
    offset?: number;
  }) {
    const queryParams = new URLSearchParams();
    if (params?.q) queryParams.set('q', params.q);
    if (params?.rank) queryParams.set('rank', params.rank);
    if (params?.limit) queryParams.set('limit', String(params.limit));
    if (params?.offset) queryParams.set('offset', String(params.offset));
    
    const query = queryParams.toString();
    const res = await fetch(`${API_BASE}/taxa${query ? `?${query}` : ''}`);
    if (!res.ok) throw new Error(`Failed to fetch taxa: ${res.status}`);
    return res.json() as Promise<PaginatedResponse<Taxon>>;
  },

  async getTaxon(taxonId: string) {
    const res = await fetch(`${API_BASE}/taxa/${taxonId}`);
    if (!res.ok) throw new Error(`Failed to fetch taxon: ${res.status}`);
    return res.json() as Promise<Taxon>;
  },

  // Observations
  async getObservations(params?: {
    taxon_id?: string;
    start?: string;
    end?: string;
    bbox?: string; // "min_lon,min_lat,max_lon,max_lat"
    limit?: number;
    offset?: number;
  }) {
    const queryParams = new URLSearchParams();
    if (params?.taxon_id) queryParams.set('taxon_id', params.taxon_id);
    if (params?.start) queryParams.set('start', params.start);
    if (params?.end) queryParams.set('end', params.end);
    if (params?.bbox) queryParams.set('bbox', params.bbox);
    if (params?.limit) queryParams.set('limit', String(params.limit));
    if (params?.offset) queryParams.set('offset', String(params.offset));
    
    const query = queryParams.toString();
    const res = await fetch(`${API_BASE}/observations${query ? `?${query}` : ''}`);
    if (!res.ok) throw new Error(`Failed to fetch observations: ${res.status}`);
    return res.json() as Promise<PaginatedResponse<Observation>>;
  },

  // Statistics
  async getStats() {
    const res = await fetch(`${API_BASE}/stats`);
    if (!res.ok) throw new Error(`Failed to fetch stats: ${res.status}`);
    return res.json() as Promise<MINDEXStats>;
  },
};
```

### Step 2: Statistics Endpoint (Already Added ✅)

The `/api/mindex/stats` endpoint is already implemented in MINDEX API and accessible through:
- Direct: `http://localhost:8000/api/mindex/stats`
- Gateway: `http://localhost:3000/api/natureos/mindex/stats`

The endpoint returns:
- Database counts (taxa, observations, external IDs)
- Data breakdown by source
- Observation quality metrics
- ETL sync status (`running` | `idle` | `unknown`)
- Date ranges and additional metadata

### Step 3: Create MINDEX Tab Component

Create `app/mindex/page.tsx` or `app/(tabs)/mindex/page.tsx`:

```typescript
// app/(tabs)/mindex/page.tsx
'use client';

import { useState, useEffect } from 'react';
import { mindexApi, type MINDEXStats } from '@/lib/integrations/mindex';
import { MINDEXStatsWidget } from '@/components/mindex/MINDEXStatsWidget';
import { TaxonSearchWidget } from '@/components/mindex/TaxonSearchWidget';
import { ObservationMapWidget } from '@/components/mindex/ObservationMapWidget';
import { ETLSyncStatusWidget } from '@/components/mindex/ETLSyncStatusWidget';

export default function MINDEXPage() {
  const [stats, setStats] = useState<MINDEXStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadStats();
    // Refresh stats every 30 seconds
    const interval = setInterval(loadStats, 30000);
    return () => clearInterval(interval);
  }, []);

  const loadStats = async () => {
    try {
      const data = await mindexApi.getStats();
      setStats(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load statistics');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mx-auto"></div>
          <p className="mt-4 text-muted-foreground">Loading MINDEX data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center text-destructive">
          <p>Error: {error}</p>
          <button onClick={loadStats} className="mt-4 btn-primary">
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-4 space-y-6">
      <div className="mb-6">
        <h1 className="text-3xl font-bold">MINDEX - Fungal Database</h1>
        <p className="text-muted-foreground mt-2">
          Comprehensive fungal taxonomy, observations, and genomic data
        </p>
      </div>

      {/* Stats Overview */}
      {stats && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <MINDEXStatsWidget stats={stats} />
        </div>
      )}

      {/* ETL Sync Status */}
      <ETLSyncStatusWidget />

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Taxon Search */}
        <div className="bg-card rounded-lg border p-6">
          <h2 className="text-xl font-semibold mb-4">Search Fungal Species</h2>
          <TaxonSearchWidget />
        </div>

        {/* Observation Map */}
        <div className="bg-card rounded-lg border p-6">
          <h2 className="text-xl font-semibold mb-4">Observation Map</h2>
          <ObservationMapWidget />
        </div>
      </div>
    </div>
  );
}
```

### Step 4: Create Widget Components

#### Stats Widget (`components/mindex/MINDEXStatsWidget.tsx`)

```typescript
'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { MINDEXStats } from '@/lib/integrations/mindex';

interface Props {
  stats: MINDEXStats;
}

export function MINDEXStatsWidget({ stats }: Props) {
  const formatNumber = (num: number) => num.toLocaleString();

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Total Taxa</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{formatNumber(stats.total_taxa)}</div>
          <p className="text-xs text-muted-foreground mt-1">Fungal species</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Observations</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{formatNumber(stats.total_observations)}</div>
          <p className="text-xs text-muted-foreground mt-1">
            {formatNumber(stats.observations_with_location)} with location
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">With Images</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{formatNumber(stats.observations_with_images)}</div>
          <p className="text-xs text-muted-foreground mt-1">Visual observations</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Data Sources</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-1">
            {Object.entries(stats.taxa_by_source).map(([source, count]) => (
              <div key={source} className="flex justify-between text-sm">
                <span className="capitalize">{source}</span>
                <span className="font-medium">{formatNumber(count)}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </>
  );
}
```

#### ETL Sync Status Widget (`components/mindex/ETLSyncStatusWidget.tsx`)

```typescript
'use client';

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { mindexApi } from '@/lib/integrations/mindex';

export function ETLSyncStatusWidget() {
  const [status, setStatus] = useState<'running' | 'idle' | 'unknown'>('unknown');
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, 10000); // Check every 10 seconds
    return () => clearInterval(interval);
  }, []);

  const checkStatus = async () => {
    try {
      const stats = await mindexApi.getStats();
      // @ts-ignore - etl_status is added by the API
      setStatus(stats.etl_status || 'unknown');
      setLastUpdate(new Date());
    } catch (err) {
      setStatus('unknown');
    }
  };

  const statusColors = {
    running: 'bg-green-500',
    idle: 'bg-gray-500',
    unknown: 'bg-yellow-500',
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>ETL Sync Status</span>
          <Badge className={statusColors[status]}>
            {status.toUpperCase()}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Data is continuously synced from iNaturalist, GBIF, MycoBank, and other sources.
          </p>
          {lastUpdate && (
            <p className="text-xs text-muted-foreground">
              Last checked: {lastUpdate.toLocaleTimeString()}
            </p>
          )}
          {status === 'running' && (
            <div className="flex items-center gap-2 text-sm">
              <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-primary"></div>
              <span>Sync in progress...</span>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
```

#### Taxon Search Widget (`components/mindex/TaxonSearchWidget.tsx`)

```typescript
'use client';

import { useState } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { mindexApi, type Taxon } from '@/lib/integrations/mindex';

export function TaxonSearchWidget() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Taxon[]>([]);
  const [loading, setLoading] = useState(false);

  const search = async () => {
    if (!query.trim()) return;
    
    setLoading(true);
    try {
      const response = await mindexApi.getTaxa({ q: query, limit: 20 });
      setResults(response.data);
    } catch (err) {
      console.error('Search failed:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input
          placeholder="Search for fungal species..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
        />
        <Button onClick={search} disabled={loading}>
          {loading ? 'Searching...' : 'Search'}
        </Button>
      </div>

      {results.length > 0 && (
        <div className="space-y-2 max-h-96 overflow-y-auto">
          {results.map((taxon) => (
            <div
              key={taxon.id}
              className="p-3 border rounded-lg hover:bg-accent cursor-pointer"
            >
              <div className="font-medium">{taxon.canonical_name}</div>
              {taxon.common_name && (
                <div className="text-sm text-muted-foreground">
                  {taxon.common_name}
                </div>
              )}
              <div className="text-xs text-muted-foreground mt-1">
                Source: {taxon.source} • Rank: {taxon.rank}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

#### Observation Map Widget (`components/mindex/ObservationMapWidget.tsx`)

```typescript
'use client';

import { useState, useEffect } from 'react';
import { mindexApi, type Observation } from '@/lib/integrations/mindex';
import dynamic from 'next/dynamic';

// Dynamically import map component to avoid SSR issues
const Map = dynamic(() => import('@/components/mindex/ObservationMap'), {
  ssr: false,
});

export function ObservationMapWidget() {
  const [observations, setObservations] = useState<Observation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadObservations();
  }, []);

  const loadObservations = async () => {
    try {
      const response = await mindexApi.getObservations({ limit: 100 });
      setObservations(response.data.filter(obs => obs.location !== null));
    } catch (err) {
      console.error('Failed to load observations:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="h-96 flex items-center justify-center">Loading map...</div>;
  }

  return (
    <div className="h-96 rounded-lg overflow-hidden border">
      <Map observations={observations} />
    </div>
  );
}
```

### Step 5: Add to Navigation

Update your navigation/tabs configuration to include MINDEX:

```typescript
// app/(tabs)/_layout.tsx or navigation config
export const tabs = [
  // ... existing tabs
  {
    name: 'mindex',
    href: '/mindex',
    icon: 'database', // or appropriate icon
    label: 'MINDEX',
  },
];
```

### Step 6: Environment Variables

Add to `.env.local` in NatureOS:

```bash
# MINDEX API Configuration
MINDEX_API_BASE_URL=http://localhost:8000/api/mindex
MINDEX_API_KEY=your-api-key
```

## Data Flow

```
User Interaction (NatureOS UI)
    ↓
React Components (Widgets)
    ↓
MINDEX API Client (lib/integrations/mindex.ts)
    ↓
NatureOS API Gateway (/api/natureos/mindex/*)
    ↓ (BFF Proxy with auth)
MINDEX API (localhost:8000/api/mindex/*)
    ↓
PostgreSQL Database (localhost:5434)
    ↓
ETL Jobs (Background sync from external sources)
```

**Note**: All requests go through NatureOS API gateway - no direct calls to MINDEX from frontend.

## Real-time Updates

- **Stats Widget**: Refreshes every 30 seconds
- **ETL Status**: Checks every 10 seconds
- **Observation Map**: Can be refreshed manually or on interval
- **Taxon Search**: On-demand (user-triggered)

## Error Handling

All API calls should handle:
- Network errors (MINDEX API down)
- Rate limiting (429 responses)
- Authentication errors (401/403)
- Data validation errors

Example error handling:
```typescript
try {
  const data = await mindexApi.getTaxa({ q: query });
  // Handle success
} catch (error) {
  if (error.status === 429) {
    // Rate limited - show message, retry after delay
  } else if (error.status === 401 || error.status === 403) {
    // Auth error - check API key
  } else {
    // Generic error
  }
}
```

## Testing

1. **Verify MINDEX API is running**:
   ```bash
   curl http://localhost:8000/api/mindex/health
   ```

2. **Test API client**:
   ```typescript
   const health = await mindexApi.getHealth();
   console.log('MINDEX Health:', health);
   ```

3. **Test widgets individually** before integrating into full page

## Next Steps

1. ✅ Create API client (`lib/integrations/mindex.ts`)
2. ✅ Add statistics endpoint to MINDEX API
3. ✅ Create MINDEX tab page
4. ✅ Build widget components
5. ✅ Add to navigation
6. 🔄 Add advanced features (filters, pagination, detail views)
7. 🔄 Add data visualization charts
8. 🔄 Add export functionality

## Support

- MINDEX API Docs: `http://localhost:8000/docs`
- MINDEX Health: `http://localhost:8000/api/mindex/health`
- ETL Status: Check Docker containers: `docker ps --filter "name=mindex"`

## Notes

- MINDEX API uses `/api/mindex` prefix for all routes
- All requests require `X-API-Key` header
- Database is continuously updated by ETL jobs
- Rate limits are handled automatically by the API client
- Checkpoint/resume system ensures data sync continues after interruptions
