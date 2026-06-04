#!/usr/bin/env python3
"""Verify NAS migration progress, start mindex-api, test health/sine/storage."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/home/mycosoft/mindex"
BACKUP = "/var/lib/mindex-nas-local-backup-20260604005520"


def load_creds() -> None:
    for creds in (ROOT / ".credentials.local", ROOT.parent.parent / "MAS" / "mycosoft-mas" / ".credentials.local"):
        if creds.is_file():
            for line in creds.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def run(ssh, cmd: str, timeout: int = 180) -> str:
    print(f">>> {cmd[:110]}")
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if out:
        print(out[-3000:])
    if err.strip():
        print(err[-800:], file=sys.stderr)
    return out


def token_from_env_file(ssh) -> str:
    out = run(ssh, f"grep -E '^MINDEX_INTERNAL' {REMOTE}/.env | head -3", 20)
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip() in (
                "MINDEX_INTERNAL_TOKENS",
                "MINDEX_INTERNAL_SECRET",
                "MINDEX_INTERNAL_SERVICE_TOKEN",
            ):
                return v.strip().split(",")[0].strip().strip('"')
    return ""


def curl_internal(ssh, path: str, token: str) -> dict | None:
    safe_t = token.replace('"', '\\"')
    out = run(
        ssh,
        f'curl -sf -m 25 -H "X-Internal-Token: {safe_t}" "http://127.0.0.1:8000{path}"',
        40,
    )
    try:
        return json.loads(out.strip().split("\n")[-1] if "\n" in out else out)
    except json.JSONDecodeError:
        return None


def start_api(ssh, vm_esc: str) -> None:
    run(ssh, "docker rm -f mindex-api 2>/dev/null; true", 30)
    net = run(ssh, "docker inspect mindex-postgres -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'", 20).strip()
    if not net:
        net = "mindex_mindex-network"
    run(
        ssh,
        f"cd {REMOTE} && docker run -d --name mindex-api --restart unless-stopped "
        f"--network {net} -p 8000:8000 --env-file .env "
        f"-e MINDEX_DB_HOST=db -e MINDEX_DB_PORT=5432 -e REDIS_URL=redis://redis:6379/0 "
        f"-e NAS_MOUNT_PATH=/mnt/nas/mindex "
        f"-v {REMOTE}/mindex_api:/app/mindex_api:ro "
        f"-v {REMOTE}/mindex_etl:/app/mindex_etl:ro "
        f"-v /mnt/nas/mindex:/mnt/nas/mindex:rw "
        f"mindex-etl:latest uvicorn mindex_api.main:app --host 0.0.0.0 --port 8000",
        120,
    )
    run(ssh, "sleep 10; docker logs mindex-api 2>&1 | tail -20", 60)


def wait_rsync_done(ssh, vm_esc: str, max_minutes: int = 180) -> bool:
    for i in range(max_minutes):
        rsync_n = run(ssh, "pgrep -c rsync 2>/dev/null || echo 0", 20).strip()
        nas_du = run(ssh, "du -s /mnt/nas/mindex/Library 2>/dev/null | awk '{print $1}'", 120).strip()
        local_du = run(ssh, f"du -s {BACKUP}/Library 2>/dev/null | awk '{{print $1}}'", 120).strip()
        backup_exists = run(ssh, f"test -d {BACKUP} && echo yes || echo no", 10).strip()
        print(f"[poll {i+1}] rsync={rsync_n} nas_kb={nas_du} local_kb={local_du} backup={backup_exists}")
        try:
            if nas_du and local_du and int(nas_du) >= int(local_du) * 95 // 100:
                if rsync_n == "0":
                    return True
        except ValueError:
            pass
        if backup_exists == "no" and rsync_n == "0":
            return True
        time.sleep(60)
    return False


def main() -> int:
    quick = "--quick" in sys.argv
    wait_min = int(os.environ.get("RSYNC_WAIT_MINUTES", "120"))
    load_creds()
    import paramiko

    vm_pass = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    if not vm_pass:
        print("VM_PASSWORD missing", file=sys.stderr)
        return 1
    vm_esc = vm_pass.replace("'", "'\"'\"'")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=vm_pass, timeout=30)

    run(ssh, "findmnt -n -o FSTYPE /mnt/nas/mindex; df -h / /mnt/nas/mindex | tail -2", 30)

    # Single rsync worker
    if run(ssh, "pgrep -c rsync 2>/dev/null || echo 0", 10).strip() != "1":
        run(ssh, f"echo '{vm_esc}' | sudo -S pkill -f 'rsync.*mindex-nas-local-backup' 2>/dev/null; sleep 2; true", 30)
        run(
            ssh,
            f"echo '{vm_esc}' | sudo -S bash -c 'nohup rsync -a {BACKUP}/Library/ /mnt/nas/mindex/Library/ >> /tmp/rsync-nas.log 2>&1 &'",
            30,
        )

    health = run(ssh, "curl -sf -m 5 http://127.0.0.1:8000/api/mindex/health 2>/dev/null || echo down", 15)
    if "down" in health or not health.strip().startswith("{"):
        print("Starting API...")
        start_api(ssh, vm_esc)

    token = token_from_env_file(ssh)
    if not token:
        print("WARN: no internal token in .env", file=sys.stderr)

    results: dict[str, object] = {}
    for path in (
        "/api/mindex/health",
        "/api/mindex/library/storage",
        "/api/mindex/sine/status",
        "/api/mindex/library/blobs?category=acoustic&limit=3",
    ):
        data = curl_internal(ssh, path, token)
        results[path] = data
        print(f"OK {path}" if data else f"FAIL {path}")

    # Wait for rsync if still migrating
    if not quick and run(ssh, f"test -d {BACKUP} && echo yes || echo no", 10).strip() == "yes":
        print(f"Waiting for library rsync to NAS (up to {wait_min}m)...")
        if wait_rsync_done(ssh, vm_esc, max_minutes=wait_min):
            run(ssh, f"echo '{vm_esc}' | sudo -S rm -rf {BACKUP}", 600)
            run(ssh, "df -h / | tail -1", 20)
            run(ssh, f"cd {REMOTE} && docker compose up -d --build api 2>&1 | tail -15", 600)
        else:
            print("Rsync still in progress — backup kept", file=sys.stderr)

    run(ssh, "du -sh /mnt/nas/mindex/Library 2>/dev/null; df -h / /mnt/nas/mindex | tail -2", 120)

    ssh.close()

    failed = [p for p, d in results.items() if not d]
    if failed:
        print("Failed endpoints:", failed, file=sys.stderr)
        return 1
    print("=== verify_and_start_api OK ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
