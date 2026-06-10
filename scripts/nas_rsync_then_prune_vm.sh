#!/usr/bin/env bash
# Run ON MINDEX VM 189 after CIFS is mounted. Copies Library to NAS, deletes local backup, starts API.
set -euo pipefail

BACKUP="${MIGRATE_LOCAL_BACKUP:-/var/lib/mindex-nas-local-backup-20260604005520}"
MOUNT="${NAS_MOUNT_PATH:-/mnt/nas/mindex}"
LOG="/tmp/mindex-nas-rsync.log"
REMOTE="/home/mycosoft/mindex"

log() { echo "[$(date -Iseconds)] $*"; }

if ! findmnt -n -o FSTYPE "$MOUNT" | grep -qiE 'cifs|smb'; then
  log "ERROR: $MOUNT is not CIFS — abort"
  exit 1
fi

log "NAS: $(df -h "$MOUNT" | tail -1)"
log "Root before: $(df -h / | tail -1)"

if [[ -d "$BACKUP/Library" ]]; then
  log "rsync $BACKUP/Library -> $MOUNT/Library"
  rsync -a --info=stats2 "$BACKUP/Library/" "$MOUNT/Library/" | tee -a "$LOG"
  LOCAL_N=$(find "$BACKUP/Library/acoustic" -type f 2>/dev/null | wc -l)
  NAS_N=$(find "$MOUNT/Library/acoustic" -type f 2>/dev/null | wc -l)
  log "files local=$LOCAL_N nas=$NAS_N"
  if [[ "$NAS_N" -ge "$(( LOCAL_N * 95 / 100 ))" ]]; then
    log "Removing local backup $BACKUP"
    rm -rf "$BACKUP"
  else
    log "WARN: NAS count low — backup kept"
  fi
fi

log "Root after: $(df -h / | tail -1)"
cd "$REMOTE"
docker compose up -d --build api 2>&1 | tail -20
docker compose restart api 2>&1 | tail -5
log "done"
