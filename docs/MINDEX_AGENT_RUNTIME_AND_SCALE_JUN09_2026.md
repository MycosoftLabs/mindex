# MINDEX Agent Runtime, Storage Coordination & Petabyte Scale — June 9, 2026

> **What MINDEX is:** the central database of Mycosoft — the canonical catalog
> that continuously scrapes, normalizes, and organizes the world's environmental
> and biological data (biodiversity, genetics, chemistry, earth-scale sensors,
> acoustics, civic/infrastructure) into our own structured Postgres tables, then
> mirrors the hot subset to Supabase for the website, the Earth Simulator, and
> the WorldView API, and tiers the bulk library to NAS and AWS cold storage.

This document explains what we had, what changed in this upgrade, and how MINDEX
now scales from ~40–50 TB local to a petabyte.

---

## 1. The problem we fixed

MINDEX already had ~60 source connectors and ~20 ETL jobs, but **no dependable
runtime**:

- **Two competing loops.** `mindex_etl/scheduler.py` (interval daemon) and
  `mindex_etl/aggressive_runner.py` (continuous phases) overlapped and neither
  was the clear "always-on" runtime. The aggressive runner wasn't even wired
  into `docker-compose`.
- **No durable state.** The scheduler tracked `last_run` in an in-memory dict —
  every restart re-ran everything from scratch and lost all history. The API's
  ETL run tracking (`_active_runs`) was also in-memory only.
- **No per-source isolation.** One slow or failing source could stall an entire
  cycle. Backoff was ad-hoc string-sniffing inside the loop.
- **Partial cloud/storage coordination.** A `SupabaseClient` and tiered
  `StorageManager` existed, but nothing systematically pushed deltas to Supabase
  or reconciled the Postgres ↔ Supabase ↔ NAS tiers.
- **Backups were stubs.** `s3_collector.py` / `nas_collector.py` exited 0;
  there was no `pg_dump`, no snapshots, no Glacier tiering.

## 2. The upgrade: one orchestrator, many sub-agents

MINDEX now runs as **one orchestrator agent supervising many sub-agents** — one
sub-agent per source, plus system agents for storage. This is the
"Mindex agent that orchestrates sub-agents for each source" model.

```
                     ┌─────────────────────────────────────────────┐
                     │           MindexOrchestrator                 │
                     │  (python -m mindex_etl.orchestrator)         │
                     │  • picks due agents by priority              │
                     │  • bounded thread pool (MINDEX_AGENT_CONCURRENCY)
                     │  • per-host concurrency groups (no hammering)│
                     │  • heartbeat + livestream events             │
                     └───────────────┬─────────────────────────────┘
            ┌──────────────┬─────────┼───────────┬──────────────┬─────────────┐
            ▼              ▼         ▼           ▼              ▼             ▼
      SourceAgent    SourceAgent  SourceAgent  ...        SystemAgent   SystemAgent
        gbif           inat_obs     genbank              supabase_sync  aws_backup_pg
   (every 24h)      (every 5m)   (every 24h)            (every 5m)      (nightly)
        │               │            │                      │              │
        ▼               ▼            ▼                      ▼              ▼
   existing job     existing job  existing job        Postgres→Supabase  pg_dump→S3
   run_all["gbif"]  run_all[...]  run_all[...]         delta push         (Std-IA→Glacier)
```

Each **sub-agent** (`mindex_etl/agents/base.py::SourceAgent`) owns:

- its **own schedule** (`schedule_seconds`, seeded from the proven legacy policy);
- its **own watermark/cursor** (opaque JSON owned by the underlying job);
- **health + exponential backoff**, with distinct cooldowns for HTTP 429
  (rate-limited), HTTP 503 (downtime → 6 h), and generic errors;
- a **concurrency group** so agents that hit the same upstream host (NCBI, RSC,
  GBIF, iNaturalist) never run in parallel and trip rate limits.

Crucially, **we did not rewrite the 60 connectors or 20 jobs.** The registry
(`mindex_etl/agents/registry.py`) *wraps* the existing
`mindex_etl.jobs.run_all` registry and the earth-data sync functions. New
sources automatically become agents.

### System agents (storage coordination)

Maintenance is modeled as agents too, so it's scheduled, observable, and
backed-off just like ingestion:

| Agent | Cadence | What it does |
|-------|---------|--------------|
| `supabase_sync` | 5 min | Push new/updated hot rows Postgres → Supabase mirror (incremental, ledger-tracked) |
| `aws_backup_pg` | nightly | `pg_dump -Fc` of the canonical DB → S3 (Standard-IA, lifecycle → Glacier) |
| `aws_backup_nas_manifest` | nightly | Snapshot the NAS file manifest → S3; opt-in Deep-Archive offload of cold dirs |
| `s3_inventory` | daily | Inventory the S3 cold bucket into `network.storage_node` for federation |

## 3. Durable state — "always on" survives restarts

New `etl.*` schema (migration
`migrations/20260609_mindex_agent_runtime_jun09_2026.sql`, also self-healed at
startup):

- **`etl.source_agent`** — registry + live state (schedule, status, `next_run_at`,
  `cooldown_until`, `consecutive_failures`, totals, watermark). On restart the
  orchestrator resumes each agent exactly where it left off.
- **`etl.agent_run`** — append-only run history (the activity feed / livestream).
- **`etl.orchestrator_heartbeat`** — single row; the API reports the runtime
  alive if `last_beat_at` is within 120 s.
- **`etl.backup_log`** — every pg_dump / NAS manifest / Glacier offload / S3
  inventory.

If Postgres is briefly unreachable the orchestrator **keeps running in memory**
and reconnects later — it never crashes.

## 4. The livestream

The old "live state" was a read-only snapshot. The upgraded livestream is driven
by the orchestrator:

- **`GET /api/mindex/agents/stream`** — SSE: orchestrator heartbeat + agent
  summary + newest `etl.agent_run` rows every 3 s.
- Events are also published to Redis (`mindex:etl:events` channel +
  `mindex:etl:stream`) for low-latency consumers; if Redis is absent the API
  falls back to DB polling and the stream still works.

Control plane (internal-token auth, under `/api/mindex` and
`/api/mindex/internal`):

| Endpoint | Purpose |
|----------|---------|
| `GET /agents` | every sub-agent with live state + orchestrator liveness |
| `GET /agents/heartbeat` | is the runtime alive? cycle, stats |
| `GET /agents/{name}` | one agent + its recent runs |
| `POST /agents/{name}/run` | nudge an agent to run on the next tick |
| `POST /agents/{name}/pause` · `/resume` | enable/disable an agent live |
| `GET /agents/runs` | recent run history |
| `GET /agents/backups` | AWS/NAS backup history |
| `GET /agents/stream` | SSE livestream |

## 5. Storage coordination: Postgres ↔ Supabase ↔ NAS ↔ AWS

```
External APIs ─▶ Postgres (HOT, <5ms, canonical)
                    │
                    ├─▶ Supabase  (WARM, <50ms global)  ← website / Earth Simulator / WorldView
                    │     via supabase_sync agent, app.supabase_sync_ledger watermarks
                    │
                    ├─▶ NAS        (COLD, LAN bulk)       ← library, scrapes, training, images
                    │     via StorageManager, app.storage_manifest
                    │
                    └─▶ AWS S3     (OFF-SITE COLD/ARCHIVE)← pg_dump, NAS manifest, Glacier
                          via aws_backup_* agents, etl.backup_log, network.storage_node
```

- **Supabase mirror** (`mindex_etl/sync/supabase_sync.py`): incremental, per-table
  high-water mark on an auto-detected timestamp column, tolerant of missing
  tables. Configure the table set with `MINDEX_SUPABASE_SYNC_TABLES`.
- **AWS backups** (`mindex_etl/backup/aws_backup.py`): real boto3 `pg_dump` → S3
  and NAS manifest snapshots; both no-op cleanly without boto3/creds/bucket.

## 6. Scaling 40–50 TB → 1 PB

The 4-tier model is what makes a petabyte affordable — only a small hot working
set lives on expensive fast storage:

| Tier | Tech | Target size | Role |
|------|------|-------------|------|
| HOT | Postgres/PostGIS (NVMe) | 1–5 TB | Canonical structured rows, spatial/search indexes |
| WARM | Supabase | 0.5–2 TB | Global read mirror for web/agents |
| COLD | NAS (UniFi, CIFS) | up to ~178 TB | Library blobs, scrapes, training, images |
| ARCHIVE | AWS S3 + Glacier Deep Archive | **petabyte-elastic** | Off-site backups + bulk cold object store |

**The path to 1 PB:**

1. **Keep Postgres lean.** Hot rows + indexes only; bulk blobs go to NAS by
   policy (ingest already refuses to write library files unless the CIFS mount
   is healthy). Time-series/raw archives roll off to NAS via `StorageManager`.
2. **NAS as the bulk floor.** Expand the UniFi array toward ~178 TB. Every file
   is tracked in `app.storage_manifest` / `network.storage_node`.
3. **AWS S3 + Glacier as the elastic ceiling.** This is where the petabyte lives.
   - `aws_backup_pg` keeps restorable DB snapshots off-site (Standard-IA → Glacier
     via bucket lifecycle).
   - `aws_backup_nas_manifest` snapshots the NAS index nightly (manifest-only, so
     it scales to billions of files without copying them inline) and can offload
     flagged **cold** directories to **Glacier Deep Archive** (`MINDEX_NAS_OFFLOAD_DIRS`,
     bounded by `MINDEX_NAS_OFFLOAD_MAX_GB`) — ~$1/TB/month.
   - `s3_inventory` reconciles the bucket back into `network.storage_node`.
4. **Federation view.** `network.storage_node` already models NAS + edge + S3
   nodes with capacity/usage, so MINDEX always knows where every byte lives.

**Recommended S3 bucket lifecycle (set once on `AWS_S3_MINDEX_BUCKET`):**

- `backups/pg/*`: Standard-IA → Glacier at 30 days → expire at 365 days.
- `nas-cold/*`: Deep Archive immediately.
- `backups/nas-manifest/*`: Standard-IA → expire at 180 days.

## 7. Operate it

```bash
# Always-on runtime (replaces `python -m mindex_etl.scheduler`)
docker compose --profile etl up -d          # starts mindex-etl = the orchestrator
docker compose logs -f etl                  # watch the orchestrator + sub-agents

# Direct / ad-hoc
python -m mindex_etl.orchestrator            # supervised forever
python -m mindex_etl.orchestrator --once     # one tick (cron / n8n)
python -m mindex_etl.orchestrator --list     # list all agents

# Backups (also run automatically as system agents)
python -m mindex_etl.backup.aws_backup pg        # pg_dump -> S3
python -m mindex_etl.backup.aws_backup nas       # NAS manifest -> S3
python -m mindex_etl.backup.aws_backup snapshot  # both

# Supabase mirror
python -m mindex_etl.sync.supabase_sync

# Observe (internal-token auth)
curl -H "X-Internal-Token: $TOK" http://192.168.0.189:8000/api/mindex/agents
curl -H "X-Internal-Token: $TOK" http://192.168.0.189:8000/api/mindex/agents/stream
```

### Configuration (see `.env.example`)

| Var | Purpose |
|-----|---------|
| `MINDEX_AGENT_CONCURRENCY` | Max sub-agents running at once (default 4) |
| `MINDEX_AGENT_MAX_PAGES` | Per-run page bound so no agent starves others (default 25) |
| `MINDEX_DOMAIN_MODE` | `all` (all life) or `fungi` for iNat/GBIF |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Enable the Supabase mirror |
| `MINDEX_SUPABASE_SYNC_TABLES` | Override which tables mirror |
| `AWS_S3_MINDEX_BUCKET` / `AWS_REGION` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Enable AWS backups |
| `S3_ENDPOINT_URL` | S3-compatible endpoint (MinIO/Wasabi) |
| `MINDEX_NAS_OFFLOAD_DIRS` / `MINDEX_NAS_OFFLOAD_MAX_GB` | Opt-in Glacier offload of cold NAS dirs |

## 8. Backward compatibility

- `mindex_etl/scheduler.py` and `mindex_etl/aggressive_runner.py` remain
  importable; the orchestrator is the recommended runtime.
- The existing ETL API (`/sync`, `/etl/run`, `/etl-status`, `/pipeline/stream`)
  is unchanged. The new `/agents/*` endpoints are additive.
- The `etl.*` schema is created idempotently by both the migration and the
  orchestrator's `ensure_schema()`, so existing deployments need no manual step.

## 9. Files added / changed

**New:**
- `mindex_etl/agents/` — `base.py`, `state.py`, `events.py`, `registry.py`,
  `orchestrator.py`, `__init__.py`
- `mindex_etl/orchestrator.py` — CLI entrypoint
- `mindex_etl/sync/supabase_sync.py` — Postgres → Supabase mirror
- `mindex_etl/backup/aws_backup.py` — pg_dump/NAS/Glacier backups
- `mindex_api/routers/agents.py` — control + livestream API
- `migrations/20260609_mindex_agent_runtime_jun09_2026.sql`
- `tests/test_agent_runtime.py`

**Changed:**
- `mindex_etl/jobs/s3_collector.py` — real (optional) S3 inventory
- `mindex_api/routers/__init__.py`, `mindex_api/main.py` — register `agents_router`
- `docker-compose.yml` — `etl` service now runs the orchestrator
- `Dockerfile` — add `postgresql-client`, `boto3`, `redis`
- `.env.example` — Supabase / AWS / orchestrator vars
```
