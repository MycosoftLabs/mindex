#!/usr/bin/env python3
"""Check MINDEX container logs - Feb 11, 2026"""

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

print("Container logs (last 30 lines):")
stdin, stdout, stderr = ssh.exec_command("docker logs mindex-api --tail 30 2>&1", timeout=30)
print(stdout.read().decode())

print("\nNetwork inspection:")
stdin, stdout, stderr = ssh.exec_command("docker inspect mindex-api --format '{{json .NetworkSettings.Networks}}' | head -2", timeout=30)
print(stdout.read().decode())

print("\nCan mindex-api reach postgres?")
stdin, stdout, stderr = ssh.exec_command("docker exec mindex-api ping -c 1 mindex-postgres 2>&1 || echo 'Cannot ping'", timeout=30)
print(stdout.read().decode())

print("\nTrying to access postgres from container:")
stdin, stdout, stderr = ssh.exec_command("docker exec mindex-api curl -s http://mindex-postgres:5432 2>&1 || echo 'Connection tested'", timeout=30)
print(stdout.read().decode())

print("\nCheck available endpoints:")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/docs 2>/dev/null | grep -o '<title>.*</title>' || curl -s http://localhost:8000/openapi.json | head -5", timeout=30)
print(stdout.read().decode())

ssh.close()
