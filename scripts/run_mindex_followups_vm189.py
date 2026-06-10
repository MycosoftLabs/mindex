#!/usr/bin/env python3
"""Run all MINDEX remediation follow-ups on VM 189."""
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

PUSH_FILES = [
    "mindex_etl/sources/chemspider.py",
    "mindex_etl/jobs/publications.py",
    "mindex_etl/jobs/hq_media_ingestion.py",
    "docker-compose.yml",
    "migrations/20260610_pgvector_optional_JUN10_2026.sql",
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


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 600) -> tuple[str, int]:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = (stdout.read() + stderr.read()).decode(errors="replace")
    return out, stdout.channel.recv_exit_status()


def push_all(ssh: paramiko.SSHClient) -> None:
    sftp = ssh.open_sftp()
    for rel in PUSH_FILES:
        sftp.put(str(REPO / rel.replace("/", os.sep)), f"{REMOTE}/{rel}")
        print(f"pushed {rel}")
    sftp.close()


def main() -> int:
    pw = load_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="mycosoft", password=pw, timeout=30)
    push_all(ssh)

    print("\n=== pgvector optional migration ===")
    out, code = run(
        ssh,
        f"cd {REMOTE} && sudo docker exec -i mindex-postgres psql -U mycosoft -d mindex "
        f"-v ON_ERROR_STOP=0 < migrations/20260610_pgvector_optional_JUN10_2026.sql",
        timeout=120,
    )
    print(out[-1200:])

    print("\n=== recreate earth-sync (healthcheck fix) ===")
    out, _ = run(
        ssh,
        f"cd {REMOTE} && sudo docker compose --profile earth up -d --force-recreate earth-sync && sleep 20",
        timeout=180,
    )
    print(out[-800:])
    out, _ = run(ssh, "sudo docker ps --filter name=mindex-earth-sync --format '{{.Names}} {{.Status}}'", timeout=30)
    print(out.strip())

    print("\n=== chemspider smoke (limit 20) ===")
    out, code = run(
        ssh,
        "sudo docker exec mindex-etl timeout 300 python -m mindex_etl.jobs.sync_chemspider_compounds --limit 20 2>&1 | tail -25",
        timeout=360,
    )
    print(out)
    print(f"exit {code}")

    print("\n=== genbank backfill (max-pages 10) ===")
    out, code = run(
        ssh,
        "sudo docker exec mindex-etl timeout 600 python -m mindex_etl.jobs.sync_genbank_genomes --max-pages 10 2>&1 | tail -15",
        timeout=660,
    )
    print(out)
    print(f"exit {code}")

    print("\n=== publications gbif+pubmed (1 term) ===")
    out, code = run(
        ssh,
        "sudo docker exec mindex-etl timeout 180 python -c "
        "\"import asyncio; from mindex_etl.jobs.publications import run_publications_etl; "
        "print(asyncio.run(run_publications_etl(max_per_term=5, search_terms=['fungi'], "
        "sources=['pubmed','gbif'])))\" 2>&1 | tail -15",
        timeout=240,
    )
    print(out)
    print(f"exit {code}")

    print("\n=== NAS mount check ===")
    out, _ = run(ssh, "sudo docker exec mindex-etl ls -la /mnt/nas/mindex 2>&1 | head -5", timeout=30)
    print(out)

    print("\n=== hq_media ingest (limit 3, live) ===")
    out, code = run(
        ssh,
        "sudo docker exec mindex-etl timeout 300 python -m mindex_etl.jobs.hq_media_ingestion --limit 3 2>&1 | tail -20",
        timeout=360,
    )
    sys.stdout.buffer.write(out.encode("utf-8", errors="replace"))
    sys.stdout.buffer.write(f"\nexit {code}\n".encode())
    sys.stdout.flush()

    counts_sql = (
        "SELECT 'compounds', count(*) FROM bio.compound "
        "UNION ALL SELECT 'sequences', count(*) FROM bio.genetic_sequence "
        "UNION ALL SELECT 'genomes', count(*) FROM bio.genome "
        "UNION ALL SELECT 'publications', count(*) FROM core.publications "
        "UNION ALL SELECT 'media_images', count(*) FROM media.image "
        "UNION ALL SELECT 'taxa_fungi', count(*) FROM core.taxon WHERE kingdom='Fungi';"
    )
    print("\n=== final counts ===")
    out, _ = run(ssh, f"sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c \"{counts_sql}\"", timeout=60)
    print(out)

    print("\n=== container health ===")
    out, _ = run(ssh, "sudo docker ps --filter name=mindex --format '{{.Names}} {{.Status}}'", timeout=30)
    print(out)

    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
