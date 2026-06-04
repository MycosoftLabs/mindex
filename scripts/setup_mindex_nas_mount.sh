#!/usr/bin/env bash
# Mount UniFi NAS library storage on MINDEX VM (189).
# Library acoustic files MUST NOT live on the VM root disk.
#
# Usage (on VM as root or with sudo):
#   export NAS_CIFS_URL="//192.168.0.105/mycosoft.com/mindex"
#   export NAS_SMB_USER=mycosoft
#   export NAS_SMB_PASSWORD='...'
#   sudo -E bash scripts/setup_mindex_nas_mount.sh
#
# Optional: migrate data that was written to a local folder before mount:
#   sudo -E MIGRATE_LOCAL_BACKUP=/var/lib/mindex-nas-local-backup bash scripts/setup_mindex_nas_mount.sh

set -euo pipefail

NAS_CIFS_URL="${NAS_CIFS_URL:-//192.168.0.105/mycosoft.com/mindex}"
MOUNT_POINT="${NAS_MOUNT_PATH:-/mnt/nas/mindex}"
CREDS_FILE="${NAS_CREDS_FILE:-/etc/samba/mindex-nascreds}"
MIGRATE_LOCAL_BACKUP="${MIGRATE_LOCAL_BACKUP:-}"

echo "=== MINDEX NAS library mount ==="
echo "Share:      $NAS_CIFS_URL"
echo "Mount point: $MOUNT_POINT"

apt-get update -qq
apt-get install -y -qq cifs-utils

USE_CREDS="$CREDS_FILE"
if [[ -f "$CREDS_FILE" ]] && [[ -z "${NAS_SMB_PASSWORD:-}" ]]; then
  echo "Using existing credentials file: $CREDS_FILE"
elif [[ -f /etc/samba/mycosoft-nas.creds ]] && [[ -z "${NAS_SMB_PASSWORD:-}" ]]; then
  USE_CREDS="/etc/samba/mycosoft-nas.creds"
  echo "Using existing credentials file: $USE_CREDS"
elif [[ -n "${NAS_SMB_PASSWORD:-}" ]]; then
  mkdir -p "$(dirname "$CREDS_FILE")"
  cat > "$CREDS_FILE" <<EOF
username=${NAS_SMB_USER:-mycosoft}
password=${NAS_SMB_PASSWORD}
domain=WORKGROUP
EOF
  chmod 600 "$CREDS_FILE"
  USE_CREDS="$CREDS_FILE"
else
  echo "Set NAS_SMB_PASSWORD or create $CREDS_FILE or /etc/samba/mycosoft-nas.creds" >&2
  exit 1
fi

mkdir -p "$(dirname "$MOUNT_POINT")"
if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
  FSTYPE=$(findmnt -n -o FSTYPE "$MOUNT_POINT" || true)
  if [[ "$FSTYPE" == "cifs" || "$FSTYPE" == "smb3" ]]; then
    echo "Already mounted ($FSTYPE) at $MOUNT_POINT"
    df -h "$MOUNT_POINT"
    exit 0
  fi
  echo "Unmounting non-NAS mount ($FSTYPE) at $MOUNT_POINT..."
  umount "$MOUNT_POINT" || true
fi

if [[ -d "$MOUNT_POINT" ]] && [[ -n "$(ls -A "$MOUNT_POINT" 2>/dev/null || true)" ]]; then
  if [[ -z "$MIGRATE_LOCAL_BACKUP" ]]; then
    BACKUP="/var/lib/mindex-nas-local-backup-$(date +%Y%m%d%H%M%S)"
    echo "Moving local data off mount point -> $BACKUP"
    mv "$MOUNT_POINT" "$BACKUP"
    MIGRATE_LOCAL_BACKUP="$BACKUP"
  fi
fi

mkdir -p "$MOUNT_POINT"

FSTAB_LINE="$NAS_CIFS_URL $MOUNT_POINT cifs credentials=$USE_CREDS,uid=1000,gid=1000,iocharset=utf8,file_mode=0664,dir_mode=0775,vers=3.0,nofail,x-systemd.automount 0 0"
if ! grep -qF "$MOUNT_POINT" /etc/fstab 2>/dev/null; then
  echo "$FSTAB_LINE" >> /etc/fstab
  echo "Added fstab entry"
else
  echo "fstab already references $MOUNT_POINT"
fi

mount -t cifs "$NAS_CIFS_URL" "$MOUNT_POINT" -o "credentials=$USE_CREDS,uid=1000,gid=1000,iocharset=utf8,file_mode=0664,dir_mode=0775,vers=3.0"

if ! mountpoint -q "$MOUNT_POINT"; then
  echo "Mount failed. Try alternate share: NAS_CIFS_URL=//192.168.0.105/mycosoft/mindex" >&2
  exit 1
fi

mkdir -p "$MOUNT_POINT/Library/acoustic" "$MOUNT_POINT/archive" "$MOUNT_POINT/training" "$MOUNT_POINT/scrapes"
chown -R mycosoft:mycosoft "$MOUNT_POINT" 2>/dev/null || true

if [[ -n "$MIGRATE_LOCAL_BACKUP" && -d "$MIGRATE_LOCAL_BACKUP/Library" ]]; then
  echo "Migrating Library/ from $MIGRATE_LOCAL_BACKUP to NAS (rsync)..."
  rsync -a --info=progress2 "$MIGRATE_LOCAL_BACKUP/Library/" "$MOUNT_POINT/Library/"
  echo "Migration pass complete. Verify free space on NAS:"
fi

df -h "$MOUNT_POINT"
findmnt "$MOUNT_POINT"
echo "=== NAS mount OK ==="
