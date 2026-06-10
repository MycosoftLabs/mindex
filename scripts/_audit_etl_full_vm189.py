"""Full MINDEX ETL audit on VM 189: counts + ETL log errors."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import paramiko

CREDS = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local")

SQL_AUDIT = r"""
\pset format unaligned
\pset tuples_only on

SELECT '---TAXA_BY_SOURCE---';
SELECT COALESCE(source,'(null)'), count(*)::text FROM core.taxon GROUP BY source ORDER BY count(*) DESC;

SELECT '---TAXA_KINGDOM_FUNGI---';
SELECT count(*)::text FROM core.taxon WHERE kingdom = 'Fungi';

SELECT '---TAXA_WITH_DEFAULT_PHOTO---';
SELECT count(*)::text FROM core.taxon WHERE metadata ? 'default_photo';

SELECT '---OBSERVATIONS---';
SELECT count(*)::text FROM obs.observation;

SELECT '---COMPOUNDS_TOTAL---';
SELECT count(*)::text FROM bio.compound;

SELECT '---COMPOUNDS_BY_SOURCE---';
SELECT COALESCE(source,'(null)'), count(*)::text FROM bio.compound GROUP BY source ORDER BY count(*) DESC;

SELECT '---COMPOUNDS_PUBCHEM---';
SELECT count(*)::text FROM bio.compound WHERE pubchem_id IS NOT NULL;

SELECT '---COMPOUNDS_CHEMSPIDER---';
SELECT count(*)::text FROM bio.compound WHERE chemspider_id IS NOT NULL;

SELECT '---GENETIC_SEQUENCES---';
SELECT count(*)::text FROM bio.genetic_sequence;

SELECT '---GENETIC_BY_SOURCE---';
SELECT COALESCE(source,'(null)'), count(*)::text FROM bio.genetic_sequence GROUP BY source ORDER BY count(*) DESC;

SELECT '---GENOMES---';
SELECT count(*)::text FROM bio.genome;

SELECT '---PUBLICATIONS---';
SELECT count(*)::text FROM core.publications;

SELECT '---PUBLICATIONS_BY_SOURCE---';
SELECT COALESCE(source,'(null)'), count(*)::text FROM core.publications GROUP BY source ORDER BY count(*) DESC;

SELECT '---MEDIA_IMAGES---';
SELECT count(*)::text FROM media.image;

SELECT '---TAXON_COMPOUND_LINKS---';
SELECT count(*)::text FROM bio.taxon_compound;

SELECT '---TAXON_PUBLICATION_LINKS---';
SELECT count(*)::text FROM bio.publication_taxon;

SELECT '---FUNGIDB_GENOMES---';
SELECT count(*)::text FROM bio.genome WHERE source = 'fungidb';

SELECT '---SCHEMA_TABLES---';
SELECT schemaname||'.'||tablename FROM pg_tables
WHERE schemaname IN ('core','bio','obs','civic')
ORDER BY schemaname, tablename;
"""


def load_password() -> str:
    for line in CREDS.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() in ("VM_PASSWORD", "VM_SSH_PASSWORD"):
                return v.strip()
    return os.environ.get("VM_PASSWORD", "")


def run():
    pw = load_password()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.0.189", username="mycosoft", password=pw, timeout=20)

    results: dict[str, str] = {}

    def ssh(cmd: str, timeout: int = 120) -> str:
        i, o, e = c.exec_command(cmd, timeout=timeout)
        out = (o.read() or b"").decode(errors="replace")
        err = (e.read() or b"").decode(errors="replace")
        return (out + err).strip()

    results["docker_ps"] = ssh(
        "sudo docker ps --format '{{.Names}}|{{.Status}}' | grep -E 'mindex|postgres'"
    )

    # ETL log scan — last 2000 lines, job outcomes and errors
    log_tail = ssh("sudo docker logs mindex-etl --tail 2000 2>&1", timeout=60)
    results["etl_log_tail"] = log_tail[-12000:]

    job_lines = []
    error_lines = []
    for line in log_tail.splitlines():
        if "Job " in line and ("completed" in line or "failed" in line):
            job_lines.append(line.strip())
        if any(
            x in line.lower()
            for x in (
                "error",
                "failed",
                "exception",
                "traceback",
                "does not exist",
                "module",
                "import",
            )
        ):
            if "ERROR" in line or "failed" in line.lower() or "Traceback" in line:
                error_lines.append(line.strip())

    results["job_outcomes"] = "\n".join(job_lines[-80:])
    results["error_samples"] = "\n".join(error_lines[-60:])

    # Per-job grep in full log (last 5000)
    for job in (
        "pubchem",
        "chemspider",
        "genetics",
        "publications",
        "hq_media",
        "taxon_photos",
        "ancestry",
        "fungidb",
        "traits",
        "theyeasts",
        "fusarium",
        "mushroom_world",
        "mycobank",
        "gbif_complete",
        "kingdom_backfill",
    ):
        hits = ssh(
            f"sudo docker logs mindex-etl --tail 5000 2>&1 | grep -i '{job}' | tail -8"
        )
        if hits:
            results[f"log_{job}"] = hits

    # SQL via postgres container
    sql_escaped = SQL_AUDIT.replace("'", "'\"'\"'")
    sql_cmd = (
        f"sudo docker exec -i mindex-postgres psql -U mindex -d mindex -v ON_ERROR_STOP=0 <<'EOSQL'\n"
        f"{SQL_AUDIT}\nEOSQL"
    )
    results["sql_audit"] = ssh(sql_cmd, timeout=90)

    # Env keys (no secrets)
    results["env_keys"] = ssh(
        "grep -E '^(NCBI|PUBCHEM|CHEMSPIDER|SEMANTIC|INAT|GBIF|LOCAL_DATA|MINDEX)' "
        "/home/mycosoft/mindex/.env 2>/dev/null | sed -E 's/=(.*)/=***/' || true"
    )

    # Check if bio/compound tables exist when counts fail
    results["migration_check"] = ssh(
        "sudo docker exec mindex-postgres psql -U mindex -d mindex -c "
        "\"\\dt bio.*\" 2>&1"
    )

    c.close()

    print("=" * 60)
    print("MINDEX ETL FULL AUDIT — VM 189")
    print("=" * 60)
    for key in (
        "docker_ps",
        "env_keys",
        "sql_audit",
        "migration_check",
        "job_outcomes",
        "error_samples",
    ):
        print(f"\n### {key} ###\n")
        print(results.get(key, "(none)"))

    for key in sorted(results):
        if key.startswith("log_"):
            print(f"\n### {key} ###\n")
            print(results[key])


if __name__ == "__main__":
    run()
