#!/usr/bin/env python3
"""
Apply MINDEX taxa ETL remediation on VM 189 (P0–P2).

- Ensures NAS scrapes directory exists
- Updates .env domain modes + LOCAL_DATA_DIR
- Pulls latest code, rebuilds ETL container
- Kicks one-shot verification jobs and optional bulk syncs in background
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAS_CREDS = Path(os.environ.get("MAS_REPO", r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas")) / ".credentials.local"
HOST = os.environ.get("MINDEX_VM_HOST", "192.168.0.189")
REMOTE_REPO = os.environ.get("MINDEX_REMOTE_REPO", "/home/mycosoft/mindex")


def load_password() -> str:
    pw = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    if pw:
        return pw
    if MAS_CREDS.exists():
        for line in MAS_CREDS.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in ("VM_PASSWORD", "VM_SSH_PASSWORD"):
                    return v.strip()
    sys.exit("VM_PASSWORD missing — load .credentials.local first")


def ssh_run(ssh, cmd: str, timeout: int = 600) -> tuple[str, str, int]:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    return out, err, code


def main() -> int:
    import paramiko

    password = load_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {HOST}...")
    ssh.connect(HOST, username="mycosoft", password=password, timeout=30)

    steps = [
        ("mkdir scrapes", "sudo mkdir -p /mnt/nas/mindex/scrapes/work/inat /mnt/nas/mindex/scrapes/work/mycobank && sudo chown -R mycosoft:mycosoft /mnt/nas/mindex/scrapes 2>/dev/null || mkdir -p /mnt/nas/mindex/scrapes/work/inat"),
        ("git pull", f"cd {REMOTE_REPO} && git fetch origin && git pull --ff-only origin main 2>/dev/null || git pull --ff-only 2>/dev/null || true"),
        (
            "patch .env",
            f"""cd {REMOTE_REPO} && for kv in 'INAT_DOMAIN_MODE=fungi' 'GBIF_DOMAIN_MODE=fungi' 'LOCAL_DATA_DIR=/mnt/nas/mindex/scrapes/work'; do
  key="${{kv%%=*}}"; val="${{kv#*=}}";
  if grep -q "^$key=" .env 2>/dev/null; then sed -i "s|^$key=.*|$key=$val|" .env; else echo "$key=$val" >> .env; fi
done && grep -E 'INAT_DOMAIN_MODE|GBIF_DOMAIN_MODE|LOCAL_DATA_DIR|INAT_API_TOKEN' .env | sed 's/INAT_API_TOKEN=.*/INAT_API_TOKEN=<set>/'""",
        ),
        (
            "rebuild etl",
            f"cd {REMOTE_REPO} && sudo docker compose build etl && sudo docker compose --profile etl up -d etl",
        ),
        ("wait etl", "sleep 8"),
        (
            "verify jobs once",
            "sudo docker exec mindex-etl python -m mindex_etl.scheduler --once --max-pages 5 2>&1 | tail -40",
        ),
        (
            "taxa counts",
            "sudo docker exec mindex-postgres psql -U mindex -d mindex -c \"SELECT source, count(*) FROM core.taxon GROUP BY source ORDER BY count DESC;\"",
        ),
    ]

    for label, cmd in steps:
        print(f"\n=== {label} ===")
        out, err, code = ssh_run(ssh, cmd)
        if out.strip():
            print(out.strip())
        if err.strip():
            print(err.strip())
        if code != 0 and label in ("rebuild etl", "git pull"):
            print(f"WARNING: step {label} exit {code}")

    # Background bulk syncs (P1) — non-blocking on VM
    bulk_cmds = [
        "sudo docker exec -d mindex-etl python -m mindex_etl.jobs.sync_inat_taxa --domain-mode fungi --max-pages 500",
        "sudo docker exec -d mindex-etl python -m mindex_etl.jobs.sync_mycobank_taxa",
    ]
    print("\n=== starting P1 bulk syncs (background) ===")
    for cmd in bulk_cmds:
        out, err, _ = ssh_run(ssh, cmd)
        print(cmd)
        if err:
            print(err.strip())

    ssh.close()
    print("\nRemediation script finished. Monitor: docker logs -f mindex-etl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
