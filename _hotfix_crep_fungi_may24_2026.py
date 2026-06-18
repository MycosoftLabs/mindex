#!/usr/bin/env python3
"""Hotfix CREP fungi filters on MINDEX VM 189."""

import os
import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8")

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = os.environ.get("VM_PASSWORD", "")
ROOT = os.path.dirname(os.path.abspath(__file__))

FILES = [
    ("mindex_api/routers/observations.py", "/home/mycosoft/mindex/mindex_api/routers/observations.py"),
    ("mindex_api/routers/fungal_overlays.py", "/home/mycosoft/mindex/mindex_api/routers/fungal_overlays.py"),
]


def run(ssh, cmd: str, timeout: int = 120) -> str:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode()
    err = stderr.read().decode()
    return out + (f"\n{err}" if err.strip() else "")


def main() -> None:
    if not VM_PASS:
        print("ERROR: VM_PASSWORD not set")
        sys.exit(1)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30)

    sftp = ssh.open_sftp()
    for rel, remote in FILES:
        local = os.path.join(ROOT, rel.replace("/", os.sep))
        sftp.put(local, remote)
        print(f"uploaded {remote}")
        container_path = f"/app/{rel.replace(chr(92), '/')}"
        print(run(ssh, f"docker cp {remote} mindex-api:{container_path}"))
    sftp.close()

    print(run(ssh, "docker restart mindex-api", timeout=60))
    time.sleep(8)

    print("health:", run(ssh, "curl -s http://localhost:8000/api/mindex/health"))
    print(
        "sd bbox:",
        run(
            ssh,
            "set -a; . /home/mycosoft/mindex/.env; set +a; curl -s 'http://localhost:8000/api/mindex/observations?"
            "bbox=-117.6,32.5,-116.0,33.5&limit=3' -H 'X-API-Key: $MINDEX_API_KEY'",
        )[:600],
    )
    print(
        "sd fungi:",
        run(
            ssh,
            "set -a; . /home/mycosoft/mindex/.env; set +a; curl -s 'http://localhost:8000/api/mindex/observations?"
            "bbox=-117.6,32.5,-116.0,33.5&kingdom=Fungi&limit=3' -H 'X-API-Key: $MINDEX_API_KEY'",
        )[:600],
    )

    ssh.close()
    print("done")


if __name__ == "__main__":
    main()
