#!/usr/bin/env python3
"""Verify MINDEX follow-ups and apply small fixes on VM 189."""
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


def main() -> int:
    pw = load_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="mycosoft", password=pw, timeout=30)
    sftp = ssh.open_sftp()
    for rel in [
        "mindex_etl/jobs/publications.py",
        "migrations/20260610_pgvector_optional_JUN10_2026.sql",
    ]:
        sftp.put(str(REPO / rel.replace("/", os.sep)), f"{REMOTE}/{rel}")
    sftp.close()

    print(run(ssh, f"cd {REMOTE} && sudo docker exec -i mindex-postgres psql -U mycosoft -d mindex -v ON_ERROR_STOP=0 < migrations/20260610_pgvector_optional_JUN10_2026.sql")[-1500:])

    print("\n=== gbif publications smoke ===")
    print(run(ssh, "sudo docker exec mindex-etl timeout 120 python -c \"import asyncio; from mindex_etl.jobs.publications import run_publications_etl; print(asyncio.run(run_publications_etl(max_per_term=3, search_terms=['fungi'], sources=['gbif'])))\" 2>&1 | tail -8")[-1200:])

    sql = (
        "SELECT 'compounds', count(*) FROM bio.compound "
        "UNION ALL SELECT 'chemspider', count(*) FROM bio.compound WHERE chemspider_id IS NOT NULL "
        "UNION ALL SELECT 'sequences', count(*) FROM bio.genetic_sequence "
        "UNION ALL SELECT 'genomes', count(*) FROM bio.genome "
        "UNION ALL SELECT 'publications', count(*) FROM core.publications "
        "UNION ALL SELECT 'pubmed', count(*) FROM core.publications WHERE source='pubmed' "
        "UNION ALL SELECT 'gbif_lit', count(*) FROM core.publications WHERE source='gbif' "
        "UNION ALL SELECT 'media_images', count(*) FROM media.image "
        "UNION ALL SELECT 'taxa_fungi', count(*) FROM core.taxon WHERE kingdom='Fungi';"
    )
    print("\n=== counts ===")
    print(run(ssh, f'sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c "{sql}"'))
    print("\n=== health ===")
    print(run(ssh, "sudo docker ps --filter name=mindex --format '{{.Names}} {{.Status}}'"))
    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
