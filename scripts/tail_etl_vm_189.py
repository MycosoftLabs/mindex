#!/usr/bin/env python3
# AWS topology correction 2026-11-27: production is AWS-only; this file's legacy on-prem references are not deployment authority. See AWS_INFRA_REFERENCE.md for its replacement and disposition. Loopback development and physical-device fixtures are separate.
"""
Tail VM 189 ETL log (filtered) without restarting anything.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit('Retired on-prem utility: use the AWS release/SSM procedure in AWS_INFRA_REFERENCE.md; no local production servers exist.')


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

    stdin, stdout, stderr = ssh.exec_command("tail -600 /home/mycosoft/mindex/etl.log", timeout=60)
    text = stdout.read().decode("utf-8", errors="replace")
    ssh.close()

    needles = (
        "mycobank",
        "mblist",
        "mblist.zip",
        "mblist.xlsx",
        "head not usable",
        "trying get",
        "downloaded to",
        "failed to parse dump",
        "openpyxl",
        "phase 1",
    )

    for ln in text.splitlines():
        low = ln.lower()
        if any(n in low for n in needles):
            print(ln)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

