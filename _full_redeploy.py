#!/usr/bin/env python3
"""Full redeploy MINDEX - Feb 11, 2026"""

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

print("\n1. Pulling latest code...")
stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && git fetch origin && git reset --hard origin/main", timeout=60)
print(stdout.read().decode())

print("\n2. Stopping old container...")
stdin, stdout, stderr = ssh.exec_command("docker stop mindex-api 2>/dev/null; docker rm mindex-api 2>/dev/null", timeout=60)
print("Stopped and removed")

print("\n3. Killing anything on port 8000 (force)...")
stdin, stdout, stderr = ssh.exec_command("sudo fuser -k -9 8000/tcp 2>/dev/null || true", timeout=30)
print(stdout.read().decode())
time.sleep(5)

print("\n3b. Verify port 8000 is free...")
stdin, stdout, stderr = ssh.exec_command("sudo ss -tlnp | grep 8000 || echo 'Port 8000 is free'", timeout=30)
print(stdout.read().decode())

print("\n4. Building new image...")
stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && docker build -t mindex-api:latest --no-cache . 2>&1 | tail -30", timeout=600)
print(stdout.read().decode())

print("\n5. Starting container...")
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
print(f"Container: {out[:12]}" if out else "")
if err:
    print(f"Error: {err}")

print("\n6. Waiting for startup...")
time.sleep(15)

print("\n7. Checking container status...")
stdin, stdout, stderr = ssh.exec_command("docker ps --filter name=mindex-api --format '{{.Names}}: {{.Status}}'", timeout=30)
print(stdout.read().decode())

print("\n8. Checking health...")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/api/mindex/health", timeout=30)
print(stdout.read().decode())

print("\n9. Testing unified search...")
stdin, stdout, stderr = ssh.exec_command("curl -s 'http://localhost:8000/api/search?q=amanita&types=taxa&limit=3'", timeout=30)
result = stdout.read().decode()
print(result[:1500] if result else "No response")

ssh.close()
print("\nDone!")
