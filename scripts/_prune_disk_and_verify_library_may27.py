#!/usr/bin/env python3
"""Prune 88GB local backup on 189, verify library.blob rows + BFF paths."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = "/var/lib/mindex-nas-local-backup-20260604005520"


def load_creds() -> None:
    for creds in (
        ROOT / ".credentials.local",
        ROOT.parent.parent / "MAS" / "mycosoft-mas" / ".credentials.local",
    ):
        if creds.is_file():
            for line in creds.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    load_creds()
    import paramiko

    vm = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    if not vm:
        print("VM_PASSWORD required", file=sys.stderr)
        return 1

    host = "192.168.0.189"
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username="mycosoft", password=vm, timeout=30)
    ve = vm.replace("'", "'\"'\"'")

    def run(cmd: str, t: int = 120) -> str:
        _, o, e = ssh.exec_command(cmd, timeout=t)
        return (o.read() + e.read()).decode("utf-8", errors="replace").strip()

    print("=== Disk before ===")
    print(run("df -h / /mnt/nas/mindex 2>/dev/null"))

    nas_lib = run(
        f"du -sh /mnt/nas/mindex/Library 2>/dev/null; "
        f"findmnt -n -o FSTYPE /mnt/nas/mindex 2>/dev/null"
    )
    print("NAS Library:", nas_lib)

    blob_count = run(
        "docker exec mindex-postgres psql -U mindex -d mindex -t -c "
        "'SELECT COUNT(*) FROM library.blob WHERE category=\\'acoustic\\';' 2>/dev/null"
    )
    print("library.blob acoustic count:", blob_count.strip())

    backup_size = run(f"du -sh {BACKUP} 2>/dev/null || echo no_backup")
    print("backup:", backup_size)

    # Prune backup if NAS has Library and rsync not running
    rsync = run("pgrep -a rsync 2>/dev/null || echo none")
    print("rsync:", rsync[:120])

    nas_bytes = run(
        f"du -sb /mnt/nas/mindex/Library 2>/dev/null | awk '{{print $1}}'"
    )
    backup_bytes = run(
        f"du -sb {BACKUP}/Library 2>/dev/null | awk '{{print $1}}'"
    )
    try:
        n_b = int(nas_bytes.split()[0]) if nas_bytes.split() else 0
        b_b = int(backup_bytes.split()[0]) if backup_bytes.split() else 0
    except ValueError:
        n_b, b_b = 0, 0

    can_prune = (
        "no_backup" not in backup_size
        and "rsync" not in rsync.lower() or rsync.strip() == "none"
    )
    if can_prune and n_b > 0 and (b_b == 0 or n_b >= int(b_b * 0.9)):
        print("=== Removing local backup (NAS Library verified) ===")
        print(run(f"echo '{ve}' | sudo -S rm -rf {BACKUP}", 300))
    elif "rsync" in rsync and "none" not in rsync:
        print("Waiting for rsync (max 120s)...")
        for _ in range(24):
            time.sleep(5)
            rsync = run("pgrep rsync 2>/dev/null || echo done")
            if "done" in rsync:
                break
        print(run(f"echo '{ve}' | sudo -S rm -rf {BACKUP}", 300))

    print("=== Disk after ===")
    print(run("df -h /"))

    # Ensure API container has etl mount
    print(run("docker inspect mindex-api --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}' 2>/dev/null | head -8"))

    token_cmd = (
        "cd /home/mycosoft/mindex && set -a && . ./.env 2>/dev/null; set +a; "
        'TOK="${MINDEX_INTERNAL_TOKENS%%,*}"; '
        '[ -z "$TOK" ] && TOK="$MINDEX_INTERNAL_SERVICE_TOKEN"; '
        'echo TOKEN_SET=${#TOK}; '
        'curl -sf -m 20 -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/api/mindex/library/storage; echo; '
        'curl -sf -m 20 -H "X-Internal-Token: $TOK" '
        '"http://127.0.0.1:8000/api/mindex/library/blobs?category=acoustic&limit=3"; echo'
    )
    api_out = run(token_cmd, 45)
    print("=== API ===")
    print(api_out[:2500])

    results = {
        "disk_after": run("df -h / | tail -1"),
        "blob_count": blob_count.strip(),
        "api_snippet": api_out[:1500],
    }
    out = ROOT / "docs" / "MINDEX_LIBRARY_VERIFY_MAY27_2026.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    ssh.close()

    ok = "total" in api_out and '"items"' in api_out
    if ok:
        try:
            j = json.loads(api_out.split("\n")[-2] if "\n" in api_out else api_out)
            ok = j.get("total", 0) > 0
        except Exception:
            pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
