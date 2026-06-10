"""Quick taxa verification on VM 189 (no long-running GBIF job)."""
from __future__ import annotations

from pathlib import Path

import paramiko

CREDS = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local")

CMDS = [
    ("etl_status", "sudo docker ps --filter name=mindex-etl --format '{{.Status}}'"),
    (
        "job_count",
        "sudo docker exec mindex-etl python -c 'from mindex_etl.jobs.run_all import create_job_registry; print(len(create_job_registry()))' 2>&1 | tail -1",
    ),
    (
        "taxa_by_source",
        "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c \"SELECT source, count(*) FROM core.taxon GROUP BY source ORDER BY count DESC\"",
    ),
    (
        "fungi_kingdom",
        "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -t -c \"SELECT count(*) FROM core.taxon WHERE kingdom='Fungi'\"",
    ),
    (
        "pleurotus",
        "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -t -c \"SELECT count(*) FROM core.taxon WHERE canonical_name ILIKE '%Pleurotus ostreatus%'\"",
    ),
]


def main():
    pw = ""
    for line in CREDS.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() in ("VM_PASSWORD", "VM_SSH_PASSWORD"):
                pw = v.strip()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.0.189", username="mycosoft", password=pw, timeout=20)
    for name, cmd in CMDS:
        i, o, e = c.exec_command(cmd, timeout=60)
        out = (o.read() or b"").decode(errors="replace").strip()
        err = (e.read() or b"").decode(errors="replace").strip()
        print(f"\n{name}:\n{out or err}")
    c.close()


if __name__ == "__main__":
    main()
