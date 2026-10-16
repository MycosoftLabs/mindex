# MycoDAO fungi-only MINDEX export — October 15, 2026

**Status:** Implemented on branch `cursor/mindex-mycodao-fungi-export-oct15`  
**Base:** `e56daa91eab4acbbe6ae08b7e8dfb10e0b553ad2`

## Routes

| Method | Path | Auth |
| --- | --- | --- |
| GET | `/api/mindex/integrations/mycodao/fungi/export` | `X-Mycodao-Fungi-Export-Key` or `X-API-Key` in `MINDEX_MYCODAO_FUNGI_EXPORT_KEYS` |
| GET | `/api/mindex/integrations/mycodao/fungi/taxa/{uuid}` | Same |

## Enforcement

- Server hard-filters `kingdom=Fungi`. Nonfungal UUID detail returns 403.
- Kingdom query override is not exposed.
- Snapshot id binds grant, filters, and high-water `updated_at`.
- Unclassified kingdom rows are excluded (never guessed as Fungi).
- Rank `species` / `sp.` / `sp` normalized; raw rank retained.
- Allowlisted DTO only — no private internal rows.

## Configure

```text
MINDEX_MYCODAO_FUNGI_EXPORT_KEYS=<purpose-scoped-read-key>
```

This key is **not** treasury, reviewer, or signer authority.
