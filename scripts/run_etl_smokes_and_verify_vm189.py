#!/usr/bin/env python3
"""Run Phase B ETL smokes + fix numpy + verify counts on VM 189."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

MAS_CREDS = Path(
    os.environ.get("MAS_REPO", r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas")
) / ".credentials.local"
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


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 600) -> tuple[str, int]:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = (stdout.read() + stderr.read()).decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    return out, code


def push_files(ssh: paramiko.SSHClient) -> None:
    repo = Path(__file__).resolve().parents[1]
    remote = "/home/mycosoft/mindex"
    files = [
        "mindex_etl/jobs/sync_genbank_genomes.py",
        "docker-compose.yml",
        "pyproject.toml",
    ]
    sftp = ssh.open_sftp()
    for rel in files:
        local = repo / rel.replace("/", os.sep)
        remote_path = f"{remote}/{rel}"
        sftp.put(str(local), remote_path)
        print(f"pushed {rel}")
    sftp.close()


def main() -> int:
    pw = load_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {HOST}...")
    ssh.connect(HOST, username="mycosoft", password=pw, timeout=30)

    print("\n=== push fixes ===")
    push_files(ssh)

    print("\n=== recreate etl (env_file + healthcheck) ===")
    out, code = run(
        ssh,
        "cd /home/mycosoft/mindex && sudo docker compose --profile etl up -d --force-recreate etl && sleep 10",
        timeout=300,
    )
    print(out[-1500:])
    if code != 0:
        print(f"WARN recreate exit {code}")

    chemspider_set, _ = run(
        ssh,
        "cd /home/mycosoft/mindex && grep -c '^CHEMSPIDER_API_KEY=.' .env 2>/dev/null || echo 0",
        timeout=30,
    )
    print(f"CHEMSPIDER_API_KEY in .env: {chemspider_set.strip()}")

    steps = [
        ("numpy fix", (
            "sudo docker exec mindex-etl pip install 'numpy==1.26.4' 'openpyxl>=3.1,<3.2' "
            "--force-reinstall -q && sudo docker exec mindex-etl python -c "
            "'import numpy; print(numpy.__version__)'"
        ), 120),
        ("restart etl", "cd /home/mycosoft/mindex && sudo docker compose --profile etl restart etl && sleep 8", 120),
        ("pubchem", (
            "sudo docker exec mindex-etl timeout 300 python -m mindex_etl.jobs.sync_pubchem_compounds "
            "--max-results 50 2>&1"
        ), 360),
        ("chemspider", (
            "sudo docker exec mindex-etl timeout 300 python -m mindex_etl.jobs.sync_chemspider_compounds "
            "--limit 25 2>&1"
        ), 360),
        ("genbank", (
            "sudo docker exec mindex-etl timeout 300 python -m mindex_etl.jobs.sync_genbank_genomes "
            "--max-pages 3 2>&1"
        ), 360),
        ("fungidb", (
            "sudo docker exec mindex-etl timeout 180 python -m mindex_etl.jobs.sync_fungidb_genomes "
            "--max-pages 2 2>&1"
        ), 240),
        ("publications", (
            'sudo docker exec mindex-etl timeout 300 python -c "'
            "import asyncio; from mindex_etl.jobs.publications import run_publications_etl; "
            "print(asyncio.run(run_publications_etl(max_per_term=15, search_terms=['fungi','mycology'])))\" 2>&1"
        ), 360),
        ("hq_media dry-run", (
            "sudo docker exec mindex-etl timeout 180 python -m mindex_etl.jobs.hq_media_ingestion "
            "--limit 10 --dry-run 2>&1"
        ), 240),
        ("pip imagehash", (
            "sudo docker exec mindex-etl pip install 'imagehash>=4.3,<5.0' 'Pillow>=10.0,<11.0' -q 2>&1"
        ), 120),
        ("scheduler import test", (
            "sudo docker exec mindex-etl python -c "
            "'import mindex_etl.scheduler; print(\"scheduler_ok\")' 2>&1"
        ), 60),
    ]

    for label, cmd, timeout in steps:
        print(f"\n=== {label} ===")
        out, code = run(ssh, cmd, timeout=timeout)
        sys.stdout.buffer.write(out[-3500:].encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(f"\n[exit {code}]\n".encode())
        if code != 0 and label in ("numpy fix", "scheduler import test"):
            print(f"FAILED: {label}")
            ssh.close()
            return 1
        if code != 0 and label == "chemspider" and chemspider_set.strip() == "0":
            print("SKIP chemspider — CHEMSPIDER_API_KEY missing in VM .env")

    counts_sql = (
        "SELECT 'compounds', count(*) FROM bio.compound "
        "UNION ALL SELECT 'sequences', count(*) FROM bio.genetic_sequence "
        "UNION ALL SELECT 'genomes', count(*) FROM bio.genome "
        "UNION ALL SELECT 'publications', count(*) FROM core.publications "
        "UNION ALL SELECT 'media_images', count(*) FROM media.image "
        "UNION ALL SELECT 'taxa_fungi', count(*) FROM core.taxon WHERE kingdom='Fungi';"
    )
    print("\n=== domain counts ===")
    out, _ = run(
        ssh,
        f"sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c \"{counts_sql}\"",
        timeout=60,
    )
    print(out)

    print("\n=== API smoke ===")
    for path in [
        "/health",
        "/api/mindex/compounds?limit=3",
        "/api/mindex/genetics?limit=3",
    ]:
        out, _ = run(ssh, f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:8000{path}", timeout=30)
        body, _ = run(ssh, f"curl -s http://localhost:8000{path} | head -c 400", timeout=30)
        print(f"{path} -> HTTP {out.strip()}")
        print(body[:400])

    print("\n=== etl status ===")
    out, _ = run(ssh, "sudo docker ps --filter name=mindex-etl --format '{{.Names}} {{.Status}}'", timeout=30)
    print(out)

    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
