# BLOCKS Scheduler — MINDEX Integration Scope

**Date:** June 11, 2026  
**Status:** No MINDEX changes required (Phase 2)

## Context

BLOCKS Scheduler Integrations Phase 2 (MYCODAO) deployed to `https://blocks.mycodao.com` with n8n hourly Google Calendar auto-import on MAS VM 188.

## MINDEX assessment

Phase 2 scheduler integrations do **not** read or write MINDEX API (`192.168.0.189:8000`) or MINDEX Postgres.

| Blocks feature | Data source |
|----------------|-------------|
| Schedule slots / EPG | VM JSON at `/opt/mycodao/data/news-channel-schedule.json` |
| Google Calendar | Google iCal / Calendar API via Blocks server |
| MAS events | Outbound HTTP to MAS orchestrator webhook |
| Audit log | Supabase (when configured) |
| Market boost (Finnhub) | Finnhub API + schedule metadata |

## Verification

During deploy verification (June 11, 2026):

- `GET http://192.168.0.189:8000/health` → `{"status":"healthy"}` (MINDEX unaffected, available for other apps)

## Future MINDEX touchpoints (not built)

If BLOCKS news segments need species/compound context from MINDEX:

- Add proxy route on Blocks or website → `MINDEX_API_URL/api/...`
- Optional n8n workflow to enrich slot metadata from MINDEX taxonomy search

No migration, ETL, or schema work needed for current production deploy.
