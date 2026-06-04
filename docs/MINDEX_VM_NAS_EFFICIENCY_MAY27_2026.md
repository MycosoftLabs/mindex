# MINDEX VM NAS Efficiency — May 27, 2026

**Status:** In progress (rsync to NAS running on VM 189)

## Policy

| Tier | Location | What |
|------|----------|------|
| **VM 189 root disk** | `/` (~97 GB) | Postgres, Redis, Qdrant, Docker metadata, code, logs only |
| **NAS (CIFS)** | `//192.168.0.105/mycosoft.com/mindex` → `/mnt/nas/mindex` | All `Library/acoustic`, archives, training blobs, scrapes |

Ingest and API **refuse** to write library files unless `GET /api/mindex/library/storage` reports `remote_nas: true` (CIFS mount).

## Credentials

Stored in `MAS/mycosoft-mas/.credentials.local` and `MINDEX/mindex/.credentials.local` (gitignored):

- `NAS_SMB_USER`, `NAS_SMB_PASSWORD`, `NAS_HOST`, `NAS_CIFS_URL`

## Automation scripts

| Script | Purpose |
|--------|---------|
| `scripts/vm_nas_heavy_offload_may27_2026.py` | From dev PC: mount CIFS, rsync, prune, env, API |
| `scripts/nas_rsync_then_prune_vm.sh` | On VM: rsync backup → NAS, delete backup, `docker compose up -d --build api` |
| `scripts/setup_mindex_nas_mount.sh` | CIFS + fstab + optional migration |
| `scripts/apply_mindex_nas_mount.py` | Remote runner for mount script |

## Verify on VM

```bash
findmnt /mnt/nas/mindex          # FSTYPE cifs
df -h /mnt/nas/mindex            # ~7 TB free
du -sh /var/lib/mindex-nas-local-backup-*   # should be gone after rsync
df -h /                          # should be << 90% after backup removed
curl -H "X-Internal-Token: ..." http://127.0.0.1:8000/api/mindex/library/storage
```

## Current incident (Jun 4, 2026)

- ~88 GB library had been on VM disk under `/var/lib/mindex-nas-local-backup-*`.
- CIFS remounted with `cifs-utils` + Morgan SMB creds.
- **rsync** to NAS in progress; pipeline log: `/tmp/nas-prune-pipeline.log`, rsync log: `/tmp/rsync-nas.log`.
- After rsync: local backup deleted automatically → ~88 GB freed on VM → API image build/restart.
