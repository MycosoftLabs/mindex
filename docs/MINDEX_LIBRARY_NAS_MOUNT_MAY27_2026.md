# MINDEX Library on NAS (not VM disk) — May 27, 2026

**Status:** Policy + scripts  
**VM:** MINDEX 192.168.0.189  
**NAS:** 192.168.0.105 (UniFi / Dream Machine pools)

---

## Problem

Ingest wrote ~87GB under `/mnt/nas/mindex/Library/acoustic` on the **VM root filesystem** because that path was a normal directory, **not** a CIFS mount to the NAS. The VM disk filled (97G/97G); Postgres migrations and API restarts failed.

**Rule:** PostgreSQL + API stay on the VM. **All library audio files** stay on the NAS.

---

## Canonical layout

| Layer | Location |
|-------|----------|
| MINDEX VM | Postgres, Redis, API, ETL containers |
| NAS mount on VM | `/mnt/nas/mindex` → `//192.168.0.105/mycosoft.com/mindex` (or `//192.168.0.105/mycosoft/mindex`) |
| Acoustic library | `/mnt/nas/mindex/Library/acoustic/{source}/{environment}/{label}/*.wav` |
| DB `library.blob.abs_path` | Same paths (resolved inside API container via bind mount) |

Docker bind (unchanged):

```yaml
volumes:
  - ${NAS_MOUNT_PATH:-/mnt/nas/mindex}:/mnt/nas/mindex:rw
```

---

## Environment (VM `.env`)

```env
NAS_HOST=192.168.0.105
NAS_MOUNT_PATH=/mnt/nas/mindex
MINDEX_NAS_DATA_DIR=/mnt/nas/mindex
# Optional explicit library root:
# MINDEX_LIBRARY_ROOT=/mnt/nas/mindex/Library/acoustic
```

Add to `.credentials.local` (never commit):

```env
NAS_SMB_USER=mycosoft
NAS_SMB_PASSWORD=<nas-password>
NAS_CIFS_URL=//192.168.0.105/mycosoft.com/mindex
```

---

## One-time mount on VM 189

From dev machine (loads creds):

```powershell
cd MINDEX\mindex
# .credentials.local must include NAS_SMB_PASSWORD
python scripts/apply_mindex_nas_mount.py
```

Or on the VM:

```bash
export NAS_CIFS_URL="//192.168.0.105/mycosoft.com/mindex"
export NAS_SMB_USER=mycosoft
export NAS_SMB_PASSWORD='...'
sudo -E bash scripts/setup_mindex_nas_mount.sh
```

The script:

1. Moves existing **local** `/mnt/nas/mindex` tree to `/var/lib/mindex-nas-local-backup-*` if needed  
2. Mounts CIFS at `/mnt/nas/mindex`  
3. `rsync`s `Library/` from backup onto the NAS share  
4. Creates `Library/acoustic`, `archive`, `training`, `scrapes` on NAS  

---

## Verify

```bash
findmnt /mnt/nas/mindex          # FSTYPE must be cifs
df -h /mnt/nas/mindex            # should show NAS size (TB), not 97G VM disk
curl -H "X-Internal-Token: $TOKEN" http://127.0.0.1:8000/api/mindex/library/storage
```

Expect `"remote_nas": true` and large `free_gb`.

Ingest **refuses** to run if mount is not CIFS/NFS (`require_nas_mount()` in `ingest_nlm_audio_p0.py`).

---

## After mount

1. `docker compose restart api` on 189  
2. Resume ingest only when `library/storage` shows `remote_nas: true`  
3. Optional: free VM disk by removing backup after rsync: `sudo rm -rf /var/lib/mindex-nas-local-backup-*`

---

## Current state (VM 189 — Jun 4, 2026)

| Item | Status |
|------|--------|
| ~87GB ingest data | Moved to `/var/lib/mindex-nas-local-backup-20260604005520` |
| `/mnt/nas/mindex` | **Temporary bind mount** from that backup (API/streaming work again) |
| CIFS to 192.168.0.105 | **Permission denied** — `/etc/samba/mycosoft-nas.creds` needs correct NAS password |
| VM root disk | ~1.6GB free after cleanup; do **not** ingest until real CIFS mount works |

**Next step for Morgan:** Update NAS SMB credentials on the VM (or add `NAS_SMB_PASSWORD` to `.credentials.local`), then:

```powershell
python scripts/apply_mindex_nas_mount.py
# or: python scripts/fix_nas_mount_vm.py
```

After CIFS succeeds, `rsync` moves `Library/` from the backup onto the NAS share. Remove the bind mount from `/etc/fstab` if you added a duplicate line for the backup path.

---

## Related

- `docs/NLM_LIBRARY_CATALOG_LABELS_MAY27_2026.md`  
- `MAS/docs/MINDEX_NAS_BLOB_STORAGE_FEB05_2026.md`  
- `mindex_etl/library/nas_mount.py`
