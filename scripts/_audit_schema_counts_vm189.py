"""Schema describe + row counts on VM 189."""
from __future__ import annotations

import os
from pathlib import Path

import paramiko

CREDS = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local")

QUERIES = [
    ("etl_db", "sudo docker exec mindex-etl sh -c 'echo DATABASE_URL=$DATABASE_URL; echo MINDEX_DATABASE_URL=$MINDEX_DATABASE_URL' | sed 's/:[^:]*@/:***@/g'"),
    ("compound_schema", r"sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c '\d bio.compound'"),
    ("genome_schema", r"sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c '\d bio.genome'"),
    ("publications_schema", r"sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c '\d core.publications'"),
    ("compound_count", "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -t -c 'SELECT count(*) FROM bio.compound'"),
    ("genome_count", "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -t -c 'SELECT count(*) FROM bio.genome'"),
    ("pubs_count", "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -t -c 'SELECT count(*) FROM core.publications'"),
    ("kingdom_dist", "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c \"SELECT kingdom, count(*) FROM core.taxon WHERE kingdom IS NOT NULL GROUP BY kingdom ORDER BY count DESC LIMIT 15\""),
    ("mindex_grants", "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c \"SELECT grantee, privilege_type, table_name FROM information_schema.role_table_grants WHERE table_schema='bio' AND grantee='mindex'\""),
    ("etl_job_logs", "sudo docker logs mindex-etl --tail 4000 2>&1 | grep -iE 'pubchem|chemspider|genetics|publications|hq_media|taxon_photos|ancestry' | grep -vi Schedule | tail -40"),
    ("genetic_tables", "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c \"SELECT table_schema, table_name FROM information_schema.tables WHERE table_name LIKE '%genetic%' OR table_name LIKE '%sequence%'\""),
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
    for name, cmd in QUERIES:
        i, o, e = c.exec_command(cmd, timeout=90)
        out = (o.read() or b"").decode(errors="replace")
        err = (e.read() or b"").decode(errors="replace")
        print(f"\n### {name} ###\n{out}{err}")
    c.close()


if __name__ == "__main__":
    main()
