#!/usr/bin/env python3
# AWS topology correction 2026-11-27: production is AWS-only; this file's legacy on-prem references are not deployment authority. See AWS_INFRA_REFERENCE.md for its replacement and disposition. Loopback development and physical-device fixtures are separate.
"""Start all containers and test - Feb 11, 2026"""

if __name__ == "__main__":
    raise SystemExit('Retired on-prem utility: use the AWS release/SSM procedure in AWS_INFRA_REFERENCE.md; no local production servers exist.')


import os
import paramiko
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

VM_PASS = os.environ.get("VM_PASSWORD")
if not VM_PASS:
    print("ERROR: VM_PASSWORD not set")
    exit(1)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)

print("1. Starting database containers...")
stdin, stdout, stderr = ssh.exec_command("docker start mindex-postgres mindex-redis mindex-qdrant 2>&1", timeout=60)
print(stdout.read().decode())

time.sleep(5)

print("2. Restarting mindex-api...")
stdin, stdout, stderr = ssh.exec_command("docker restart mindex-api", timeout=60)
print(stdout.read().decode())

time.sleep(12)

print("3. Container status:")
stdin, stdout, stderr = ssh.exec_command("docker ps --format '{{.Names}}: {{.Status}}' | grep mindex", timeout=30)
print(stdout.read().decode())

print("4. Health check:")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/api/mindex/health", timeout=30)
print(stdout.read().decode())

print("\n5. Testing unified search (q=amanita):")
stdin, stdout, stderr = ssh.exec_command("curl -s 'http://localhost:8000/api/search?q=amanita&types=taxa&limit=5'", timeout=30)
result = stdout.read().decode()
print(result[:2000] if result else "No response")

ssh.close()
print("\nDone!")
