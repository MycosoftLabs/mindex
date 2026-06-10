#!/usr/bin/env python3
"""Quick VM 189 counts for handoff doc."""
import os
import sys
from pathlib import Path

import paramiko

MAS_CREDS = Path(
    os.environ.get("MAS_REPO", r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas")
) / ".credentials.local"


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


def run(ssh, cmd: str) -> str:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=60)
    return (stdout.read() + stderr.read()).decode(errors="replace").strip()


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=load_password(), timeout=30)
    queries = {
        "kingdom_counts": (
            "SELECT kingdom, count(*) FROM core.taxon GROUP BY kingdom ORDER BY 2 DESC LIMIT 10;"
        ),
        "compound_table": (
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='bio' AND table_name='compound';"
        ),
        "genetic_sequence_table": (
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='bio' AND table_name='genetic_sequence';"
        ),
        "bio_grants": (
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE table_schema='bio' AND grantee='mindex';"
        ),
        "compounds": "SELECT count(*) FROM bio.compound;",
        "genetic_sequences": "SELECT count(*) FROM bio.genetic_sequence;",
        "genomes": "SELECT count(*) FROM bio.genome;",
        "publications": "SELECT count(*) FROM core.publications;",
        "media_images": "SELECT count(*) FROM media.image;",
        "observations": "SELECT count(*) FROM obs.observation;",
    }
    for label, sql in queries.items():
        out = run(
            ssh,
            f"sudo docker exec mindex-postgres psql -U mycosoft -d mindex -tAc \"{sql}\"",
        )
        print(f"{label}: {out}")
    print("health:", run(ssh, "curl -s http://localhost:8000/health"))
    print("etl:", run(ssh, "sudo docker ps --filter name=mindex-etl --format '{{.Status}}'"))
    ssh.close()


if __name__ == "__main__":
    main()
