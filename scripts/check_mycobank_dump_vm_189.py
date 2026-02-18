#!/usr/bin/env python3
"""
Check where MycoBank dump files are landing on VM 189.
"""

from __future__ import annotations

import os

import paramiko


VM_IP = "192.168.0.189"
VM_USER = "mycosoft"


def main() -> int:
    vm_password = os.environ.get("VM_PASSWORD")
    if not vm_password:
        print("ERROR: VM_PASSWORD not set")
        return 1

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VM_IP, username=VM_USER, password=vm_password, timeout=30)

    paths = [
        "/home/mycosoft/mindex/data/mindex_scrape/mycobank",
        "/home/mycosoft/mindex/data/mindex_scrape/mycobank/extracted",
        "/home/mycosoft/mindex/C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_scrape/mycobank",
        "/home/mycosoft/mindex/C:/Users/admin2/Desktop/MYCOSOFT/DATA/mindex_scrape/mycobank/extracted",
    ]

    for p in paths:
        cmd = f"echo '--- {p} ---'; ls -lah {p} 2>/dev/null || echo '(missing)'"
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
        print(stdout.read().decode("utf-8", errors="replace"))

    # Also check any in-progress downloads
    cmd = "ps -ef | grep -E 'mindex_etl|MBList' | grep -v grep || true"
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    print("--- processes ---")
    print(stdout.read().decode("utf-8", errors="replace"))

    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

