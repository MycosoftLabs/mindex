#!/usr/bin/env python3
"""Run publications + hq_media smokes on VM 189 after remediation."""
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


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 300) -> str:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return (stdout.read() + stderr.read()).decode(errors="replace")


def push(ssh: paramiko.SSHClient, rel: str) -> None:
    sftp = ssh.open_sftp()
    sftp.put(str(REPO / rel.replace("/", os.sep)), f"{REMOTE}/{rel}")
    sftp.close()


def main() -> int:
    pw = load_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="mycosoft", password=pw, timeout=30)

    for rel in [
        "mindex_etl/jobs/publications.py",
        "mindex_etl/jobs/hq_media_ingestion.py",
    ]:
        push(ssh, rel)

    smokes = [
        ("publications", "sudo docker exec mindex-etl timeout 240 python -m mindex_etl.jobs.publications 10"),
        ("hq_media dry-run", "sudo docker exec mindex-etl timeout 120 python -m mindex_etl.jobs.hq_media_ingestion --limit 2 --dry-run"),
    ]
    for label, cmd in smokes:
        print(f"\n=== {label} ===")
        out = run(ssh, f"{cmd} 2>&1 | tail -25", timeout=320)
        print(out)

    print("\n=== counts ===")
    sql = (
        "SELECT 'compounds', count(*) FROM bio.compound "
        "UNION ALL SELECT 'sequences', count(*) FROM bio.genetic_sequence "
        "UNION ALL SELECT 'publications', count(*) FROM core.publications "
        "UNION ALL SELECT 'media_images', count(*) FROM media.image;"
    )
    print(run(ssh, f'sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c "{sql}"', timeout=60))
    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
