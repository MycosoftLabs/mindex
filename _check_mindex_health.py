#!/usr/bin/env python3
"""Check MINDEX health - Feb 11, 2026"""

import os
import paramiko
import sys

sys.stdout.reconfigure(encoding='utf-8')

VM_PASS = os.environ.get("VM_PASSWORD")
if not VM_PASS:
    print("ERROR: VM_PASSWORD not set")
    exit(1)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)

print("Container status:")
stdin, stdout, stderr = ssh.exec_command("docker ps --filter name=mindex-api --format '{{.Names}}: {{.Status}}'", timeout=30)
print(stdout.read().decode())

print("\nHealth check:")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/api/mindex/health", timeout=30)
print(stdout.read().decode())

print("\nUnified search test (q=amanita):")
stdin, stdout, stderr = ssh.exec_command("curl -s 'http://localhost:8000/api/search?q=amanita&types=taxa&limit=3'", timeout=30)
result = stdout.read().decode()
print(result[:1500] if result else "No response")

print("\nAPI logs (last 10 lines):")
stdin, stdout, stderr = ssh.exec_command("docker logs mindex-api --tail 10 2>&1", timeout=30)
print(stdout.read().decode())

ssh.close()
