# MINDEX - Mycological Index

> **The World's Comprehensive Mycological Data Platform**

> **Version**: 2.1.0  
> **Last Updated**: 2026-02-10  
> **Port**: 8000  
> **VM**: 192.168.0.189

---

## Vision

**MINDEX** (Mycological Index) is more than a database—it's a **Decentralized Science (DeSci) platform** designed to be the world's most comprehensive repository of mycological data. It serves as the canonical data layer for all Mycosoft systems, powering AI/ML research, species identification, carbon credit tracking, and the validation of the Global Fungi Symbiosis Theory.

### What MINDEX Aspires To Be

| Domain | Description |
|--------|-------------|
| **Species Database** | Complete taxonomy of 150,000+ known fungal species with images, genome data, ecology, distribution |
| **Signal Repository** | Historical archive of electrical signals from FCI devices for pattern learning |
| **Carbon Credit Engine** | Track and verify carbon sequestration from mycelium-based materials |
| **DeSci Infrastructure** | Decentralized verification of mycological research data |
| **AI/ML Training Data** | Labeled datasets for species ID, signal classification, growth prediction |
| **Environmental Archive** | Global environmental sensor data correlated with fungal activity |

---

## Current Implementation

> **Note:** MINDEX is being built in phases. This section describes what's working today.

### What's Working Now

- **PostgreSQL Database** with species, observations, events tables
- **REST API** for CRUD operations on observations and species
- **1.2M+ Fungal Observations** synced from GBIF
- **Geocoding Pipeline** for enriching location data
- **Event Logging** for audit trail and real-time tracking
- **Redis Pub/Sub** for real-time event notifications
- **Qdrant Vector Store** for semantic search
- **ETL Jobs** for syncing external data sources

### What's Planned

- Signal pattern storage and retrieval
- Carbon credit tracking schema
- AI/ML model training pipelines
- DeSci verification protocols
- HPL/FCI device data integration
- Decentralized data replication

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              MINDEX PLATFORM                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐ │
│   │                 │     │                  │     │                     │ │
│   │  MINDEX API     │────▶│  PostgreSQL      │────▶│  GBIF / External    │ │
│   │  (FastAPI)      │     │  (Canonical DB)  │     │  Data Sources       │ │
│   │  Port 8000      │     │  Port 5432       │     │                     │ │
│   │                 │     │                  │     │                     │ │
│   └────────┬────────┘     └──────────────────┘     └─────────────────────┘ │
│            │                                                                │
│            │              ┌──────────────────┐     ┌─────────────────────┐ │
│            │              │                  │     │                     │ │
│            └─────────────▶│  Redis           │────▶│  Real-time Events   │ │
│                           │  (Pub/Sub)       │     │  (Websockets/SSE)   │ │
│                           │  Port 6379       │     │                     │ │
│                           │                  │     │                     │ │
│                           └──────────────────┘     └─────────────────────┘ │
│                                                                             │
│            ┌──────────────────────────────────────────────────────────────┐ │
│            │                                                              │ │
│            │  Qdrant Vector Store (Port 6333)                             │ │
│            │  - Species embeddings for semantic search                    │ │
│            │  - Observation description similarity                        │ │
│            │  - Signal pattern matching (planned)                         │ │
│            │                                                              │ │
│            └──────────────────────────────────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

```bash
# Clone and install
cd /opt/mycosoft/mindex
pip install -r requirements.txt

# Run migrations
python -m mindex_api.db migrate

# Start API server
uvicorn mindex_api.main:app --host 0.0.0.0 --port 8000
```

## 🐳 Docker (VM 189)

```bash
# Start all MINDEX services
docker-compose up -d

# Services started:
# - mindex-postgres (5432)
# - mindex-redis (6379)
# - mindex-qdrant (6333)
# - mindex-api (8000)
```

---

## 📡 API Endpoints

### Core Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/v1/observations` | GET | List observations with filtering |
| `/api/v1/observations` | POST | Create new observation |
| `/api/v1/observations/{id}` | GET | Get observation by ID |
| `/api/v1/observations/{id}` | PATCH | Update observation |
| `/api/v1/species` | GET | List species with taxonomy search |
| `/api/v1/species/{id}` | GET | Get species details |
| `/api/v1/events` | POST | Log event |
| `/api/v1/events/batch` | POST | Batch log events |
| `/api/v1/search` | POST | Semantic search over observations |

### Planned Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/v1/signals` | FCI signal data storage and retrieval |
| `/api/v1/signals/patterns` | Pattern matching and classification |
| `/api/v1/carbon` | Carbon credit tracking |
| `/api/v1/experiments` | GFST validation experiment data |
| `/api/v1/genomes` | Fungal genome data |

---

## 📊 Database Schema

### Current Schema

```sql
-- Species (core taxonomy)
CREATE TABLE species (
    id UUID PRIMARY KEY,
    scientific_name TEXT NOT NULL UNIQUE,
    common_name TEXT,
    family TEXT,
    genus TEXT,
    phylum TEXT,
    class TEXT,
    order_name TEXT,
    kingdom TEXT DEFAULT 'Fungi',
    description TEXT,
    image_url TEXT,
    gbif_id INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP
);

-- Observations (field sightings)
CREATE TABLE observations (
    id UUID PRIMARY KEY,
    species_id UUID REFERENCES species(id),
    latitude REAL,
    longitude REAL,
    altitude REAL,
    location_name TEXT,
    location_source TEXT,
    observer_name TEXT,
    notes TEXT,
    image_urls JSONB,
    observed_at TIMESTAMP,
    has_gps BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Events (audit trail)
CREATE TABLE events (
    id UUID PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    collector TEXT,
    data JSONB,
    timestamp TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Sensor readings (from MycoBrain devices)
CREATE TABLE sensor_readings (
    id UUID PRIMARY KEY,
    device_id TEXT NOT NULL,
    reading_type TEXT NOT NULL,
    value REAL,
    unit TEXT,
    metadata JSONB,
    timestamp TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Planned Schema Extensions

```sql
-- Signal patterns (for FCI/GFST)
CREATE TABLE signal_patterns (
    id UUID PRIMARY KEY,
    pattern_name TEXT NOT NULL,
    pattern_type TEXT,  -- 'growth', 'stress', 'environmental', 'seismic'
    species_id UUID REFERENCES species(id),
    amplitude_range NUMRANGE,
    frequency_range NUMRANGE,
    waveform_type TEXT,
    sample_data BYTEA,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Carbon credits (for DeSci)
CREATE TABLE carbon_credits (
    id UUID PRIMARY KEY,
    project_id TEXT NOT NULL,
    species_id UUID REFERENCES species(id),
    sequestration_kg REAL,
    verification_status TEXT,
    verification_hash TEXT,
    issued_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Experiments (for GFST validation)
CREATE TABLE experiments (
    id UUID PRIMARY KEY,
    experiment_name TEXT NOT NULL,
    hypothesis TEXT,
    methodology TEXT,
    species_id UUID REFERENCES species(id),
    device_ids TEXT[],
    start_date TIMESTAMP,
    end_date TIMESTAMP,
    status TEXT,
    results JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 🔗 System Integrations

### Mycorrhizae Protocol

MINDEX is the canonical data layer for the Mycorrhizae Protocol. All messages routed through Mycorrhizae can be persisted to MINDEX:

```python
# HPL example (planned)
import MINDEX

hypha sensorData = sense("temperature")
MINDEX.store(sensorData)

hypha history = MINDEX.query("sensor_readings", {
    device_id: "mycobrain-001",
    reading_type: "temperature",
    last: 100
})
```

### FCI (Fungal Computer Interface)

Signal data from FCI-enabled devices (Mushroom 1, SporeBase, MycoBrain) flows to MINDEX:

```
FCI Device → Mycorrhizae Protocol → MINDEX Storage
                                         ↓
                              Signal Pattern Analysis
                                         ↓
                                 HPL Processing
```

### MycoBrain Devices

Environmental sensor data from MycoBrain devices is logged to MINDEX:
- Temperature, humidity, pressure
- Air quality (VOC, CO2, particulates)
- Bioelectric signals (planned)

### NatureOS Dashboard

NatureOS queries MINDEX for:
- Real-time observations feed
- Species distribution maps
- Sensor data visualizations
- Experiment status

### Website CREP Dashboard

The CREP (Common Relevant Environmental Picture) dashboard displays:
- Fungal observation markers on global map
- Species distribution heatmaps
- Real-time sensor data overlays

---

## 🌍 Data Sources

### Currently Integrated

| Source | Data Type | Records |
|--------|-----------|---------|
| GBIF | Fungal observations | 1.2M+ |
| Nominatim | Geocoding | On-demand |
| MycoBrain | Sensor readings | Real-time |

### Planned Integrations

| Source | Data Type |
|--------|-----------|
| iNaturalist | Community observations |
| MyCoPortal | Herbarium specimens |
| NCBI | Genome sequences |
| FungiDB | Genomics & proteomics |
| ChemSpider | Fungal compounds |

---

## 🧬 AI/ML Capabilities (Planned)

### Species Identification

```python
# Planned API
POST /api/v1/identify
{
    "image_url": "https://example.com/mushroom.jpg"
}

# Response
{
    "species": "Pleurotus ostreatus",
    "confidence": 0.94,
    "alternatives": [...]
}
```

### Signal Classification

```python
# Planned API
POST /api/v1/signals/classify
{
    "signal_data": [...],
    "sample_rate": 1000
}

# Response
{
    "pattern_type": "growth",
    "species_match": "Pleurotus ostreatus",
    "confidence": 0.87
}
```

### Growth Prediction

```python
# Planned API
POST /api/v1/predict/growth
{
    "species_id": "uuid",
    "environmental_conditions": {
        "temperature": 25,
        "humidity": 85,
        "co2": 800
    }
}
```

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [ETL Sync Guide](./docs/ETL_SYNC_GUIDE.md) | Data synchronization from external sources |
| [NatureOS Integration](./docs/NATUREOS_INTEGRATION_GUIDE.md) | Dashboard integration |
| [MycoBrain Integration](./docs/MYCOBRAIN_INTEGRATION.md) | Device data ingestion |
| [API Reference](./docs/API_REFERENCE.md) | Complete API documentation |

---

## ⚙️ Configuration

### Environment Variables

```env
# Database
DATABASE_URL=postgresql://mycosoft:mycosoft_mindex_2026@localhost:5432/mindex
REDIS_URL=redis://localhost:6379
QDRANT_URL=http://localhost:6333

# External APIs
GBIF_API_KEY=your_gbif_key
NOMINATIM_URL=https://nominatim.openstreetmap.org

# MAS Integration
MAS_API_URL=http://192.168.0.188:8001
MYCORRHIZAE_API_URL=http://192.168.0.188:8002
```

---

## 🗺️ Roadmap

### Phase 1 - Core Database ✅
- PostgreSQL schema for species, observations, events
- REST API for CRUD operations
- GBIF data synchronization
- Geocoding pipeline

### Phase 2 - Real-time & Search ✅
- Redis pub/sub for events
- Qdrant vector store
- Semantic search

### Phase 3 - FCI Integration (Current)
- Signal pattern storage schema
- Device data ingestion
- Mycorrhizae Protocol integration

### Phase 4 - AI/ML Pipeline
- Species identification model
- Signal classification model
- Growth prediction model

### Phase 5 - DeSci Features
- Carbon credit tracking
- Verification protocols
- Decentralized data replication

---

## 📝 Changelog

### 2026-02-10
- Updated README with full Mycological Index vision
- Documented planned schema extensions
- Added AI/ML capabilities section

### 2026-01-15
- Added batch event logging endpoint
- Implemented geocoding pipeline integration
- Added Redis pub/sub for real-time events
- Integrated with CREP dashboard
- Added 1.2M+ fungal observations
- Enhanced species taxonomy with GBIF synchronization

---

## 📜 License

Copyright © 2026 Mycosoft. All rights reserved.

---

## Related Documentation

- [Mycorrhizae Protocol Overview](../../Mycorrhizae/mycorrhizae-protocol/docs/MYCORRHIZAE_PROTOCOL_OVERVIEW_FEB10_2026.md)
- [HPL Language Guide](../../Mycorrhizae/mycorrhizae-protocol/docs/HPL_LANGUAGE_GUIDE_FEB10_2026.md)
- [Fungal Computer Interface](../../Mycorrhizae/mycorrhizae-protocol/docs/FUNGAL_COMPUTER_INTERFACE_FEB10_2026.md)
- [Global Fungi Symbiosis Theory](../../Mycorrhizae/mycorrhizae-protocol/docs/GLOBAL_FUNGI_SYMBIOSIS_THEORY_FEB10_2026.md)
- [Vision Gap Analysis](../../MAS/mycosoft-mas/docs/VISION_VS_IMPLEMENTATION_GAP_ANALYSIS_FEB10_2026.md)
