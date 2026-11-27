#!/usr/bin/env python3
# AWS topology correction 2026-11-27: production is AWS-only; this file's legacy on-prem references are not deployment authority. See AWS_INFRA_REFERENCE.md for its replacement and disposition. Loopback development and physical-device fixtures are separate.
"""Get recent MINDEX API logs."""

if __name__ == "__main__":
    raise SystemExit('Retired on-prem utility: use the AWS release/SSM procedure in AWS_INFRA_REFERENCE.md; no local production servers exist.')

import os
import paramiko
import sys

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASSWORD = os.environ.get("VM_PASSWORD", "")

def main():
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(VM_HOST, username=VM_USER, password=VM_PASSWORD, timeout=15)
        
        print("[*] Getting recent API logs...")
        stdin, stdout, stderr = ssh.exec_command("docker logs mindex-api --tail 50 2>&1", timeout=30)
        logs = stdout.read().decode()
        print(logs)
        
        ssh.close()
        return 0
        
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
