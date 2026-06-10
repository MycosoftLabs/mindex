#!/usr/bin/env python3
"""
MINDEX ETL remediation Phases A–C on VM 189 (Jun 10, 2026).

Phase A — Unblock: grants, compound/genetics schema, numpy, kingdom normalization code
Phase B — Smoke: pubchem, chemspider, genbank, fungidb, publications, hq_media
Phase C — Kingdom backfill for MycoBank + scheduler once cycle

Usage (from MINDEX repo, credentials loaded):
  python scripts/apply_etl_remediation_phases_abc_vm189.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAS_CREDS = Path(
    os.environ.get(
        "MAS_REPO",
        r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas",
    )
) / ".credentials.local"
HOST = os.environ.get("MINDEX_VM_HOST", "192.168.0.189")
REMOTE_REPO = os.environ.get("MINDEX_REMOTE_REPO", "/home/mycosoft/mindex")


def load_password() -> str:
    pw = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    if pw:
        return pw
    if MAS_CREDS.exists():
        for line in MAS_CREDS.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in ("VM_PASSWORD", "VM_SSH_PASSWORD"):
                    return v.strip()
    sys.exit("VM_PASSWORD missing — load .credentials.local first")


def ssh_run(ssh, cmd: str, timeout: int = 900) -> tuple[str, str, int]:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    return out, err, code


def sftp_push(ssh, local: Path, remote: str) -> None:
    sftp = ssh.open_sftp()
    try:
        sftp.put(str(local), remote)
    finally:
        sftp.close()


def main() -> int:
    import paramiko

    password = load_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {HOST}...")
    ssh.connect(HOST, username="mycosoft", password=password, timeout=30)

    # Push critical code fixes (bind-mounted into ETL)
    patches = [
        (REPO / "mindex_etl" / "taxon_canonicalizer.py", f"{REMOTE_REPO}/mindex_etl/taxon_canonicalizer.py"),
        (REPO / "mindex_etl" / "sources" / "mycobank.py", f"{REMOTE_REPO}/mindex_etl/sources/mycobank.py"),
    ]
    print("\n=== Push code patches ===")
    for local, remote in patches:
        if local.exists():
            sftp_push(ssh, local, remote)
            print(f"  {local.name} -> {remote}")

    migrations = [
        "20260610_etl_schema_upgrade_JUN10_2026.sql",
        "20260610_pg_trgm_extension_JUN10_2026.sql",
        "0007_compounds.sql",
        "0012_genetics.sql",
        "20260603_grants_bio_obs_core.sql",
        "20260603_ledger_grants.sql",
    ]

    print("\n=== Push migration files ===")
    for name in migrations:
        local = REPO / "migrations" / name
        remote = f"{REMOTE_REPO}/migrations/{name}"
        if local.exists():
            sftp_push(ssh, local, remote)
            print(f"  {name}")

    mig_list = " ".join(f"migrations/{m}" for m in migrations)
    phase_a = [
        (
            "apply migrations",
            f"cd {REMOTE_REPO} && for f in {mig_list}; do "
            f'echo "=== $f ==="; '
            f"sudo docker exec -i mindex-postgres psql -U mycosoft -d mindex -v ON_ERROR_STOP=0 < \"$f\" || echo \"WARN: $f\"; "
            "done",
        ),
        (
            "verify compound columns",
            "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -tAc "
            "\"SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='bio' AND table_name='compound' "
            "AND column_name IN ('pubchem_id','chemspider_id','inchikey') ORDER BY 1;\"",
        ),
        (
            "verify genetic_sequence",
            "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -tAc "
            "\"SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='bio' AND table_name='genetic_sequence';\"",
        ),
        (
            "verify mindex bio grants",
            "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -tAc "
            "\"SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE table_schema='bio' AND grantee='mindex';\"",
        ),
        (
            "git pull",
            f"cd {REMOTE_REPO} && git fetch origin && git pull --ff-only origin main 2>/dev/null || true",
        ),
        (
            "rebuild etl",
            f"cd {REMOTE_REPO} && sudo docker compose build etl && sudo docker compose --profile etl up -d etl",
        ),
        ("wait etl", "sleep 12"),
        (
            "numpy + openpyxl",
            "sudo docker exec mindex-etl pip install 'numpy==1.26.4' 'openpyxl>=3.1,<3.2' --force-reinstall -q "
            "&& sudo docker exec mindex-etl python -c 'import numpy, openpyxl; print(numpy.__version__)'",
        ),
    ]

    print("\n========== PHASE A — Unblock ==========")
    for label, cmd in phase_a:
        print(f"\n--- {label} ---")
        out, err, code = ssh_run(ssh, cmd, timeout=1200)
        if out.strip():
            sys.stdout.buffer.write(out.strip()[-4000:].encode("utf-8", errors="replace"))
            sys.stdout.buffer.write(b"\n")
        if err.strip():
            sys.stdout.buffer.write(err.strip()[-2000:].encode("utf-8", errors="replace"))
            sys.stdout.buffer.write(b"\n")
        if code != 0 and label in ("rebuild etl",):
            print(f"WARNING exit {code}")

    phase_b = [
        (
            "pubchem smoke",
            "sudo docker exec mindex-etl timeout 180 python -m mindex_etl.jobs.sync_pubchem_compounds --max-results 30 2>&1 | tail -15",
        ),
        (
            "chemspider smoke",
            "sudo docker exec mindex-etl timeout 180 python -m mindex_etl.jobs.sync_chemspider_compounds --limit 15 2>&1 | tail -15",
        ),
        (
            "fungidb smoke",
            "sudo docker exec mindex-etl timeout 120 python -m mindex_etl.jobs.sync_fungidb_genomes --max-pages 2 2>&1 | tail -15",
        ),
        (
            "genbank smoke",
            "sudo docker exec mindex-etl timeout 180 python -m mindex_etl.jobs.sync_genbank_genomes --max-pages 2 2>&1 | tail -15",
        ),
        (
            "publications smoke",
            "sudo docker exec mindex-etl timeout 180 python -c \""
            "import asyncio; from mindex_etl.jobs.publications import run_publications_etl; "
            "print(asyncio.run(run_publications_etl(max_per_term=10, search_terms=['fungi','mycology'])))\" 2>&1 | tail -20",
        ),
        (
            "hq_media dry-run",
            "sudo docker exec mindex-etl timeout 120 python -m mindex_etl.jobs.hq_media_ingestion --limit 5 --dry-run 2>&1 | tail -15",
        ),
    ]

    print("\n========== PHASE B — Domain smoke ==========")
    for label, cmd in phase_b:
        print(f"\n--- {label} ---")
        out, err, code = ssh_run(ssh, cmd, timeout=300)
        combined = (out + err).strip()
        if combined:
            sys.stdout.buffer.write(combined[-2500:].encode("utf-8", errors="replace"))
            sys.stdout.buffer.write(b"\n")
        else:
            print("(no output)")

    phase_c = [
        (
            "kingdom backfill mycobank",
            "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c "
            "\"UPDATE core.taxon SET kingdom = 'Fungi' WHERE source = 'mycobank' AND (kingdom IS NULL OR kingdom = 'Undesignated');\"",
        ),
        (
            "kingdom lineage backfill",
            "sudo docker exec -d mindex-etl python -m mindex_etl.jobs.backfill_kingdom_lineage --batch 5000",
        ),
        (
            "scheduler once",
            "sudo docker exec mindex-etl timeout 600 python -m mindex_etl.scheduler --once --max-pages 5 2>&1 | tail -50",
        ),
        (
            "domain counts",
            "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c \""
            "SELECT 'taxa_fungi', count(*) FROM core.taxon WHERE kingdom='Fungi' "
            "UNION ALL SELECT 'compounds', count(*) FROM bio.compound "
            "UNION ALL SELECT 'sequences', count(*) FROM bio.genetic_sequence "
            "UNION ALL SELECT 'genomes', count(*) FROM bio.genome "
            "UNION ALL SELECT 'publications', count(*) FROM core.publications "
            "UNION ALL SELECT 'media_images', count(*) FROM media.image;\"",
        ),
        (
            "etl health",
            "sudo docker ps --filter name=mindex-etl --format '{{.Names}} {{.Status}}'",
        ),
    ]

    print("\n========== PHASE C — Backfill + scheduler ==========")
    for label, cmd in phase_c:
        print(f"\n--- {label} ---")
        out, err, code = ssh_run(ssh, cmd, timeout=700)
        if out.strip():
            print(out.strip())
        if err.strip():
            print(err.strip()[-1500:])

    ssh.close()
    print("\nRemediation Phases A–C finished. Run scripts/_audit_etl_full_vm189.py for full report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
