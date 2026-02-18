#!/usr/bin/env python3
"""
Sync selected keys from local MINDEX `.env` to VM 189 `/home/mycosoft/mindex/.env`.

This avoids putting secrets on the command line or in git-tracked templates.

Requires:
  - env var VM_PASSWORD (ssh password for mycosoft@192.168.0.189)
  - local file: MINDEX repo `.env` containing the keys
"""

from __future__ import annotations

import os
from pathlib import Path

import paramiko


VM_IP = "192.168.0.189"
VM_USER = "mycosoft"
REMOTE_ENV = "/home/mycosoft/mindex/.env"

KEYS = ("NCBI_API_KEY", "CHEMSPIDER_API_KEY")


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _upsert_lines(existing: str, updates: dict[str, str]) -> str:
    lines = existing.splitlines()
    seen = set()
    for i, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k = line.split("=", 1)[0].strip()
        if k in updates:
            lines[i] = f"{k}={updates[k]}"
            seen.add(k)
    # Append anything missing
    missing = [k for k in updates.keys() if k not in seen]
    if missing:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append("# Synced by scripts/sync_mindex_env_keys_to_vm_189.py")
        for k in missing:
            lines.append(f"{k}={updates[k]}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    vm_password = os.environ.get("VM_PASSWORD")
    if not vm_password:
        print("ERROR: VM_PASSWORD not set")
        return 1

    local_env_path = Path(__file__).resolve().parents[1] / ".env"
    if not local_env_path.exists():
        print(f"ERROR: local .env not found at {local_env_path}")
        return 1

    local_env = _parse_env(local_env_path.read_text(encoding="utf-8", errors="ignore"))
    updates = {k: local_env.get(k, "") for k in KEYS}
    missing_local = [k for k, v in updates.items() if not v]
    if missing_local:
        print(f"ERROR: missing keys in local .env: {', '.join(missing_local)}")
        return 1

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VM_IP, username=VM_USER, password=vm_password, timeout=30)
    sftp = ssh.open_sftp()

    try:
        existing = sftp.open(REMOTE_ENV, "r").read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        existing = ""

    new_text = _upsert_lines(existing, updates)
    f = sftp.open(REMOTE_ENV, "w")
    f.write(new_text.encode("utf-8"))
    f.close()
    sftp.close()

    # Don't source `.env` (it may contain JSON values which aren't shell-safe).
    # Just verify that the file contains the keys.
    cmd = (
        "cd /home/mycosoft/mindex && "
        "python3 -c \"from pathlib import Path; "
        "txt=Path('.env').read_text(encoding='utf-8', errors='ignore'); "
        "print('NCBI_API_KEY_present=', ('\\nNCBI_API_KEY=' in ('\\n'+txt))); "
        "print('CHEMSPIDER_API_KEY_present=', ('\\nCHEMSPIDER_API_KEY=' in ('\\n'+txt)));\""
    )
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    print(stdout.read().decode('utf-8', errors='replace').strip())

    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

