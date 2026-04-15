# MINDEX MYCODAO vs Mycosoft data zones — Apr 14, 2026

## Purpose

Separate **MYCODAO** tables from **Mycosoft** platform data inside the same MINDEX PostgreSQL database, while **MYCA (MAS)** continues to access **both** through internal MINDEX APIs (`X-Internal-Token`).

## PostgreSQL layout

| Schema | Role |
|--------|------|
| **`mycodao`** | Pulse / prediction-market-style aggregates, wallet stats, signals, ingestion runs, Telegram-normalized rows, Realms proposal mirrors, x402 audit — **MYCODAO product line only**. |
| **`mycosoft`** | Zone registry (`mycosoft.data_zone_registry`) and future **Mycosoft-only** cross-product metadata tables. |
| **Existing domains** (`core`, `obs`, `fusarium`, `telemetry`, `mica`, …) | Unchanged; these remain **Mycosoft MINDEX** domains (biology, earth, devices, defense analytics, etc.). |

No existing Mycosoft rows were moved; separation applies to **new** MYCODAO intelligence storage and documentation.

## MYCA discovery

Internal route (same auth as other MAS → MINDEX calls):

- `GET {MINDEX_INTERNAL_PREFIX}/meta/myca-data-catalog`

Returns:

- Rows from `mycosoft.data_zone_registry` (zone_code, display_name, pg_schema, product_line, notes).
- A static list of `mycodao.*` table names for agents and tooling.

Example (after deploy):

`GET http://192.168.0.189:8000/api/mindex/internal/meta/myca-data-catalog`  
(headers: `X-Internal-Token: <from env>`)

## MYCODAO read/write routes

All under internal prefix, e.g. `/api/mindex/internal/mycodao/...` (and backward-compatible `/api/mindex/mycodao/...` during migration):

- `GET .../mycodao/polymarket-snapshots`
- `GET .../mycodao/wallet-stats`
- `GET .../mycodao/signal-events`
- `GET .../mycodao/ingestion-runs`
- `POST .../mycodao/polymarket-snapshots`
- `POST .../mycodao/ingestion-runs/start`, `POST .../mycodao/ingestion-runs/{id}/finish`
- `POST .../mycodao/x402-audit`

## Migration

Apply SQL migration:

`migrations/0036_mycodao_mycosoft_data_zones.sql`

## Env

No new secrets. Uses existing `MINDEX_INTERNAL_TOKEN` / internal auth configuration for MINDEX.
