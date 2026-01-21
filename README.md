# MINDEX - Mycosoft Fungal Observation Database

> **Version**: 2.0.0  
> **Last Updated**: 2026-01-15T14:30:00Z  
> **Port**: 8001

## Overview

MINDEX (Mycosoft Index) is the central database for fungal observations, species data, and environmental measurements. It serves as the authoritative source for:

- Fungal species observations with GPS coordinates
- Environmental sensor data from MycoBrain devices
- Geocoded location data
- Audit trail for all data events

## 🚀 Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations
python -m mindex_api.db migrate

# Start API server
uvicorn mindex_api.main:app --host 0.0.0.0 --port 8001
```

## 🐳 Docker

```bash
docker-compose up -d
```

## 📡 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/observations` | GET | List observations |
| `/api/v1/observations` | POST | Create observation |
| `/api/v1/observations/{id}` | GET | Get observation |
| `/api/v1/observations/{id}` | PATCH | Update observation |
| `/api/v1/species` | GET | List species |
| `/api/v1/events` | POST | Log event |
| `/api/v1/events/batch` | POST | Batch log events |
| `/health` | GET | Health check |

## 📊 Database Schema

```sql
-- Observations
CREATE TABLE observations (
    id UUID PRIMARY KEY,
    species_id UUID REFERENCES species(id),
    latitude REAL,
    longitude REAL,
    location_name TEXT,
    location_source TEXT,
    observed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Species
CREATE TABLE species (
    id UUID PRIMARY KEY,
    scientific_name TEXT NOT NULL,
    common_name TEXT,
    family TEXT,
    genus TEXT
);

-- Events (Audit Trail)
CREATE TABLE events (
    id UUID PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_id TEXT,
    collector TEXT,
    timestamp TIMESTAMP,
    data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

## 🔗 Integrations

### Website Integration

The Mycosoft Website consumes MINDEX data via:
- REST API for observations and species
- Redis pub/sub for real-time event logging

### Geocoding Pipeline

Observations without GPS coordinates are enriched by the geocoding service:
1. Service queries MINDEX for observations with `has_gps=false`
2. Uses Nominatim/Photon to geocode location names
3. Updates observations with lat/lon via PATCH

### MycoBrain Integration

MycoBrain devices send environmental data that gets logged as events:
- Temperature, humidity, air quality
- Volatile compound analysis
- Location tracking

### CREP Dashboard Integration

MINDEX fungal observations are visualized on the CREP (Common Relevant Environmental Picture) dashboard:
- Real-time fungal markers on global map
- Species distribution heatmaps
- Observation clustering by density
- Pop-up details with species info and images

See the [Website Repository](https://github.com/MycosoftLabs/website) for CREP dashboard details.

## 📚 Documentation

- [ETL Sync Guide](./docs/ETL_SYNC_GUIDE.md)
- [NatureOS Integration](./docs/NATUREOS_INTEGRATION_GUIDE.md)
- [MycoBrain Integration](./docs/MYCOBRAIN_INTEGRATION.md)

## ⚙️ Configuration

```env
DATABASE_URL=postgresql://mindex:YOUR_PASSWORD@localhost:5432/mindex
REDIS_URL=redis://localhost:6379
```

## 📝 Changelog

### 2026-01-15
- Added batch event logging endpoint
- Implemented geocoding pipeline integration
- Added Redis pub/sub for real-time events
- Integrated with CREP dashboard for real-time fungal visualization
- Added 1.2M+ fungal observations to MINDEX database
- Enhanced species taxonomy with GBIF synchronization

## 📜 License

Copyright © 2026 Mycosoft. All rights reserved.
