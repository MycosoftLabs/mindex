#!/usr/bin/env python3
"""Deploy taxa ETL fixes to VM 189 via SFTP + container recreate."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAS_CREDS = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local")
HOST = "192.168.0.189"
REMOTE = "/home/mycosoft/mindex"

FILES = [
    "mindex_etl/jobs/run_all.py",
    "mindex_etl/jobs/sync_gbif_occurrences.py",
    "mindex_etl/sources/inat.py",
    "mindex_etl/config.py",
    "mindex_etl/scheduler.py",
    "docker-compose.yml",
]


def pw() -> str:
    p = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    if p:
        return p
    for line in MAS_CREDS.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() in ("VM_PASSWORD", "VM_SSH_PASSWORD"):
                return v.strip()
    sys.exit("VM_PASSWORD missing")


def run(ssh, cmd: str, timeout: int = 300) -> str:
    _, o, e = ssh.exec_command(cmd, timeout=timeout)
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    code = o.channel.recv_exit_status()
    text = (out + err).strip()
    if code != 0:
        print(f"EXIT {code}: {cmd}\n{text}")
    return text


def main() -> int:
    import paramiko

    password = pw()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="mycosoft", password=password, timeout=30)
    sftp = ssh.open_sftp()

    print("Uploading patched files...")
    for rel in FILES:
        local = REPO / rel
        remote = f"{REMOTE}/{rel.replace(chr(92), '/')}"
        sftp.put(str(local), remote)
        print(f"  {rel}")

    sftp.close()

    inat_token = os.environ.get("INAT_API_TOKEN", "").strip()
    env_patch = (
        "INAT_DOMAIN_MODE=fungi\nGBIF_DOMAIN_MODE=fungi\n"
        "LOCAL_DATA_DIR=/mnt/nas/mindex/scrapes/work\n"
    )
    if inat_token:
        env_patch += f"INAT_API_TOKEN={inat_token}\n"

    run(
        ssh,
        f"cd {REMOTE} && mkdir -p /mnt/nas/mindex/scrapes/work/inat /mnt/nas/mindex/scrapes/work/mycobank",
    )
    run(
        ssh,
        f"cd {REMOTE} && touch .env && while IFS= read -r line; do "
        f'key="${{line%%=*}}"; val="${{line#*=}}"; '
        f'if grep -q "^$key=" .env 2>/dev/null; then sed -i "s|^$key=.*|$line|" .env; else echo "$line" >> .env; fi; '
        f"done <<'EOF'\n{env_patch}EOF",
    )

    print(run(ssh, f"cd {REMOTE} && sudo docker compose --profile etl up -d etl --force-recreate"))
    print(run(ssh, "sleep 10"))
    print("=== scheduler once (smoke) ===")
    print(run(ssh, "sudo docker exec mindex-etl python -m mindex_etl.scheduler --once --max-pages 3 2>&1 | tail -30"))
    print("=== taxa by source ===")
    print(
        run(
            ssh,
            'sudo docker exec mindex-postgres psql -U mindex -d mindex -c "SELECT source, count(*) FROM core.taxon GROUP BY source ORDER BY count DESC;"',
        )
    )
    print("=== recent etl errors ===")
    print(run(ssh, "sudo docker logs mindex-etl --tail 25 2>&1 | grep -E 'failed|Failed|Error|Completed' || true"))

    print("\nStarting background bulk syncs...")
    run(ssh, "sudo docker exec -d mindex-etl python -m mindex_etl.jobs.sync_inat_taxa --domain-mode fungi --max-pages 200")
    run(ssh, "sudo docker exec -d mindex-etl python -m mindex_etl.jobs.sync_mycobank_taxa")

    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
