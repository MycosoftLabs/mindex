#!/usr/bin/env python3
"""Fix and restart MINDEX API container - Feb 11, 2026"""

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
print("Connecting to 192.168.0.189...")
ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)

print("\n1. Checking what's on port 8000...")
stdin, stdout, stderr = ssh.exec_command("sudo netstat -tlnp | grep 8000 || echo 'Nothing on 8000'", timeout=30)
print(stdout.read().decode())

print("\n2. Killing anything on port 8000...")
stdin, stdout, stderr = ssh.exec_command("sudo fuser -k 8000/tcp 2>/dev/null; sleep 2", timeout=30)
print(stdout.read().decode() or "Killed")

print("\n3. Removing old mindex-api container...")
stdin, stdout, stderr = ssh.exec_command("docker rm -f mindex-api 2>/dev/null || true", timeout=30)
print(stdout.read().decode() or "Removed")

print("\n4. Starting mindex-api on correct network...")
cmd = """docker run -d --name mindex-api -p 8000:8000 \
    --network mindex_mindex-network \
    -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex \
    -e REDIS_URL=redis://mindex-redis:6379 \
    -e QDRANT_URL=http://mindex-qdrant:6333 \
    --restart unless-stopped \
    mindex-api:latest"""
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
out = stdout.read().decode()
err = stderr.read().decode()
if out:
    print(f"Container: {out[:12]}")
if err:
    print(f"Error: {err}")

time.sleep(10)

print("\n5. Checking container status...")
stdin, stdout, stderr = ssh.exec_command("docker ps --filter name=mindex-api --format '{{.Status}}'", timeout=30)
print(stdout.read().decode())

print("\n6. Checking health...")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/api/mindex/health", timeout=30)
print(stdout.read().decode())

print("\n7. Testing unified search endpoint...")
stdin, stdout, stderr = ssh.exec_command('curl -s "http://localhost:8000/api/search?q=amanita&types=taxa&limit=3"', timeout=30)
result = stdout.read().decode()
print(result[:1000] if result else "No response")

ssh.close()
print("\nDone!")
