#!/usr/bin/env python3
"""Restart MINDEX with correct environment variables"""
import paramiko
import os
import time

VM_IP = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = os.environ.get("VM_PASSWORD", "Mushroom1!Mushroom1!")

def run_cmd(ssh, cmd, timeout=600, show=True):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    output = stdout.read().decode('utf-8', errors='ignore')
    error = stderr.read().decode('utf-8', errors='ignore')
    if show:
        for line in (output + error).strip().split('\n')[:40]:
            if line.strip():
                print(f"  {line}")
    return output + error

print("=" * 70)
print("RESTART MINDEX WITH CORRECT CONFIG")
print("=" * 70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("\n[1] Connecting...")
ssh.connect(VM_IP, username=VM_USER, password=VM_PASS, timeout=30)

print("\n[2] Remove stale container...")
run_cmd(ssh, "docker stop mindex-api 2>/dev/null; docker rm mindex-api 2>/dev/null; echo 'Done'", show=False)

print("\n[3] Start container with proper CORS origins...")
run_cmd(ssh, """docker run -d \
    --name mindex-api \
    --restart unless-stopped \
    -p 8000:8000 \
    --network mindex_mindex-network \
    -e MINDEX_DB_HOST=mindex-postgres \
    -e MINDEX_DB_PORT=5432 \
    -e MINDEX_DB_USER=mycosoft \
    -e MINDEX_DB_PASSWORD=mycosoft_mindex_2026 \
    -e MINDEX_DB_NAME=mindex \
    -e 'API_CORS_ORIGINS=["http://localhost:3000","http://localhost:3010","http://192.168.0.187:3000","http://192.168.0.188:8001"]' \
    mindex-api:latest 2>&1""")

print("\n[4] Waiting 15s for startup...")
time.sleep(15)

print("\n[5] Check container status...")
run_cmd(ssh, "docker ps --filter name=mindex-api --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'")

print("\n[6] Container logs...")
run_cmd(ssh, "docker logs mindex-api --tail 25 2>&1")

print("\n[7] Test health endpoint...")
run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health")

ssh.close()
print("\n" + "=" * 70)
print("MINDEX READY!")
print("=" * 70)
