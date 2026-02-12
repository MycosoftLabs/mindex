#!/usr/bin/env python3
"""Test MINDEX unified search - Feb 11, 2026"""

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

print("Health check:")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/api/mindex/health", timeout=30)
print(stdout.read().decode())

print("\nTest /api/mindex/unified-search?q=amanita:")
stdin, stdout, stderr = ssh.exec_command("curl -s 'http://localhost:8000/api/mindex/unified-search?q=amanita&types=taxa&limit=3'", timeout=30)
result = stdout.read().decode()
print(result[:2000] if result else "No response")

print("\nTest /api/mindex/taxa?q=amanita:")
stdin, stdout, stderr = ssh.exec_command("curl -s 'http://localhost:8000/api/mindex/taxa?q=amanita&limit=3'", timeout=30)
result = stdout.read().decode()
print(result[:1500] if result else "No response")

print("\nTest /api/mindex/compounds:")
stdin, stdout, stderr = ssh.exec_command("curl -s 'http://localhost:8000/api/mindex/compounds?limit=3'", timeout=30)
result = stdout.read().decode()
print(result[:1500] if result else "No response")

ssh.close()
