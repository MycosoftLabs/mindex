#!/usr/bin/env python3
# AWS topology correction 2026-11-27: production is AWS-only; this file's legacy on-prem references are not deployment authority. See AWS_INFRA_REFERENCE.md for its replacement and disposition. Loopback development and physical-device fixtures are separate.
"""Check MINDEX VM status"""

if __name__ == "__main__":
    raise SystemExit('Retired on-prem utility: use the AWS release/SSM procedure in AWS_INFRA_REFERENCE.md; no local production servers exist.')

import paramiko
import os

VM_IP = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = os.environ.get("VM_PASSWORD", "")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VM_IP, username=VM_USER, password=VM_PASS, timeout=30)

print("=== Docker Containers ===")
stdin, stdout, stderr = ssh.exec_command("docker ps -a", timeout=30)
print(stdout.read().decode('utf-8', errors='ignore'))

print("\n=== MINDEX API Logs (last 30) ===")
stdin, stdout, stderr = ssh.exec_command("docker logs mindex-mindex-api-1 --tail 30 2>&1 || docker logs mindex_mindex-api_1 --tail 30 2>&1 || echo 'Could not get logs'", timeout=30)
print(stdout.read().decode('utf-8', errors='ignore'))

ssh.close()
