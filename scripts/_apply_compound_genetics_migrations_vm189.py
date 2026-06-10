#!/usr/bin/env python3
"""Apply pg_trgm + 0007 + 0012 on VM 189 (one-shot)."""
import os
import sys
from pathlib import Path

import paramiko

MAS_CREDS = Path(
    os.environ.get("MAS_REPO", r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas")
) / ".credentials.local"
REPO = Path(__file__).resolve().parents[1]
REMOTE = "/home/mycosoft/mindex"


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


def main() -> int:
    pw = load_password()
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=pw, timeout=30)
    sftp = ssh.open_sftp()
    files = [
        "20260610_etl_schema_upgrade_JUN10_2026.sql",
        "20260610_pg_trgm_extension_JUN10_2026.sql",
        "0007_compounds.sql",
        "0012_genetics.sql",
    ]
    for name in files:
        local = REPO / "migrations" / name
        remote = f"{REMOTE}/migrations/{name}"
        sftp.put(str(local), remote)
        print(f"pushed {name}")
    sftp.close()

    for name in files:
        cmd = (
            f"cd {REMOTE} && sudo docker exec -i mindex-postgres "
            f"psql -U mycosoft -d mindex -v ON_ERROR_STOP=1 < migrations/{name}"
        )
        _, stdout, stderr = ssh.exec_command(cmd, timeout=180)
        out = (stdout.read() + stderr.read()).decode(errors="replace")
        code = stdout.channel.recv_exit_status()
        print(f"\n=== {name} exit={code} ===")
        print(out[-2000:])
        if code != 0:
            ssh.close()
            return 1

    verify = (
        "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -tAc "
        "\"SELECT 'compound', count(*) FROM information_schema.tables "
        "WHERE table_schema='bio' AND table_name='compound' "
        "UNION ALL SELECT 'genetic_sequence', count(*) FROM information_schema.tables "
        "WHERE table_schema='bio' AND table_name='genetic_sequence';\""
    )
    _, stdout, _ = ssh.exec_command(verify, timeout=30)
    print("\nverify:", stdout.read().decode().strip())
    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
