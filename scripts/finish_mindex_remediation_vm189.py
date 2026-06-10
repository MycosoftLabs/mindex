#!/usr/bin/env python3
"""Apply final MINDEX fixes on VM 189 and run remaining smokes."""
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

# Revoked NCBI key prefix — never log full value
_REVOKED_NCBI_PREFIXES = ("eb2264e1",)


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


def push(ssh: paramiko.SSHClient, rel: str) -> None:
    sftp = ssh.open_sftp()
    sftp.put(str(REPO / rel.replace("/", os.sep)), f"{REMOTE}/{rel}")
    sftp.close()
    print(f"pushed {rel}")


def scrub_revoked_ncbi_key(ssh: paramiko.SSHClient) -> None:
    """Comment out revoked NCBI_API_KEY in VM .env without printing secrets."""
    script = r"""
python3 <<'PY'
from pathlib import Path
p = Path("/home/mycosoft/mindex/.env")
if not p.exists():
    raise SystemExit(0)
lines = p.read_text().splitlines()
out = []
changed = False
revoked = ("eb2264e1",)
for line in lines:
    if line.startswith("NCBI_API_KEY="):
        val = line.split("=", 1)[1].strip()
        if any(val.startswith(pfx) for pfx in revoked):
            out.append("# NCBI_API_KEY=  # revoked — set NCBI_API_KEY in .env to rotate")
            changed = True
            continue
    out.append(line)
if changed:
    p.write_text("\n".join(out) + "\n")
    print("scrubbed_revoked_ncbi_key")
else:
    print("ncbi_key_ok_or_absent")
PY
"""
    out, code = run(ssh, f"cd {REMOTE} && {script}", timeout=60)
    print(out.strip())


def main() -> int:
    pw = load_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="mycosoft", password=pw, timeout=30)

    for rel in [
        "mindex_etl/jobs/publications.py",
        "mindex_etl/sources/genbank.py",
        "mindex_etl/jobs/sync_genbank_genomes.py",
        "migrations/20260610_publications_schema_JUN10_2026.sql",
        "migrations/20260610_bio_sequence_grants_JUN10_2026.sql",
    ]:
        push(ssh, rel)

    print("\n=== scrub revoked NCBI key ===")
    scrub_revoked_ncbi_key(ssh)

    for mig in (
        "migrations/20260610_publications_schema_JUN10_2026.sql",
        "migrations/20260610_bio_sequence_grants_JUN10_2026.sql",
    ):
        print(f"\n=== apply {mig} ===")
        out, code = run(
            ssh,
            f"cd {REMOTE} && sudo docker exec -i mindex-postgres psql -U mycosoft -d mindex "
            f"-v ON_ERROR_STOP=1 < {mig}",
            timeout=120,
        )
        print(out[-800:])
        if code != 0:
            ssh.close()
            return 1

    print("\n=== recreate etl (pick up .env) ===")
    run(ssh, f"cd {REMOTE} && sudo docker compose --profile etl up -d --force-recreate etl && sleep 12", timeout=180)

    # imagehash can pull numpy 2.x wheels that require X86_V2 on old VM CPUs — pin numpy 1.26.x after.
    run(
        ssh,
        "sudo docker exec mindex-etl pip install 'imagehash>=4.3,<5.0' 'Pillow>=10.0,<11.0' "
        "'numpy==1.26.4' --force-reinstall -q",
        timeout=180,
    )

    smokes = [
        ("genbank", "sudo docker exec mindex-etl timeout 240 python -m mindex_etl.jobs.sync_genbank_genomes --max-pages 2 2>&1 | tail -20", 300),
        ("publications (pubmed)", (
            "sudo docker exec mindex-etl timeout 240 python -c "
            "\"import asyncio; from mindex_etl.jobs.publications import run_publications_etl; "
            "print(asyncio.run(run_publications_etl(max_per_term=10, search_terms=['fungi'], sources=['pubmed'])))\" "
            "2>&1 | tail -20"
        ), 300),
        ("hq_media dry-run", "sudo docker exec mindex-etl timeout 120 python -m mindex_etl.jobs.hq_media_ingestion --limit 5 --dry-run 2>&1 | tail -12", 180),
    ]
    for label, cmd, timeout in smokes:
        print(f"\n=== {label} ===")
        out, code = run(ssh, cmd, timeout=timeout)
        print(out[-2000:])
        print(f"exit {code}")

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
    out, _ = run(ssh, "sudo docker ps --filter name=mindex --format '{{.Names}} {{.Status}}'", timeout=30)
    print(out)

    ssh.close()
    print("\nMINDEX remediation finish script complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
