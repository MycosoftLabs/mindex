#!/usr/bin/env python3
"""Apply full media.image schema on VM 189 (bootstrap stub → 0005 + 0006)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

MAS_CREDS = Path(
    os.environ.get("MAS_REPO", r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas")
) / ".credentials.local"
REPO = Path(__file__).resolve().parents[1]
REMOTE = "/home/mycosoft/mindex"
HOST = "192.168.0.189"

MIGRATIONS = [
    "migrations/20260610_media_image_upgrade_JUN10_2026.sql",
    # 0005 uses VECTOR(512); postgis image on 189 lacks pgvector — use VM-safe DDL instead.
    "migrations/20260610_media_image_vm189_JUN10_2026.sql",
    "migrations/20260610_hq_media_columns_vm189_JUN10_2026.sql",
]


def load_password() -> str:
    pw = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    if pw:
        return pw
    for line in MAS_CREDS.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() in ("VM_PASSWORD", "VM_SSH_PASSWORD"):
                return v.strip()
    sys.exit("VM_PASSWORD missing")


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> tuple[str, int]:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = (stdout.read() + stderr.read()).decode(errors="replace")
    return out, stdout.channel.recv_exit_status()


def main() -> int:
    pw = load_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="mycosoft", password=pw, timeout=30)
    sftp = ssh.open_sftp()
    for mig in MIGRATIONS:
        sftp.put(str(REPO / mig.replace("/", os.sep)), f"{REMOTE}/{mig}")
    sftp.close()

    for mig in MIGRATIONS:
        print(f"\n=== {mig} ===")
        out, code = run(
            ssh,
            f"cd {REMOTE} && sudo docker exec -i mindex-postgres psql -U mycosoft -d mindex "
            f"-v ON_ERROR_STOP=1 < {mig}",
            timeout=600,
        )
        print(out[-3000:])
        if code != 0:
            ssh.close()
            return 1

    print("\n=== column check ===")
    out, _ = run(
        ssh,
        "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c "
        "\"SELECT column_name FROM information_schema.columns WHERE table_schema='media' "
        "AND table_name='image' AND column_name IN ('content_hash','quality_score') ORDER BY 1;\"",
        timeout=60,
    )
    print(out)

    print("\n=== hq_media dry-run ===")
    out, _ = run(
        ssh,
        "sudo docker exec mindex-etl timeout 120 python -m mindex_etl.jobs.hq_media_ingestion --limit 2 --dry-run 2>&1 | tail -12",
        timeout=180,
    )
    print(out)
    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
