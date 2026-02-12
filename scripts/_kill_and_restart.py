#!/usr/bin/env python3
"""Kill the uvicorn process on port 8000 and restart MINDEX in Docker"""
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

def run_sudo(ssh, cmd, password, timeout=120, show=True):
    full_cmd = f"echo '{password}' | sudo -S {cmd}"
    stdin, stdout, stderr = ssh.exec_command(full_cmd, timeout=timeout, get_pty=True)
    output = stdout.read().decode('utf-8', errors='ignore')
    if show:
        for line in output.strip().split('\n')[:30]:
            if line.strip() and 'password' not in line.lower():
                print(f"  {line}")
    return output

print("=" * 70)
print("KILL UVICORN AND RESTART MINDEX")
print("=" * 70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("\n[1] Connecting...")
ssh.connect(VM_IP, username=VM_USER, password=VM_PASS, timeout=30)

print("\n[2] Finding uvicorn process on port 8000...")
run_cmd(ssh, "ps aux | grep uvicorn | grep -v grep")

print("\n[3] Killing uvicorn process 323487...")
run_sudo(ssh, "kill -9 323487 2>/dev/null || true", VM_PASS, show=False)
run_sudo(ssh, "pkill -9 uvicorn 2>/dev/null || true", VM_PASS, show=False)
time.sleep(2)
print("  Killed")

print("\n[4] Verify port 8000 is free...")
run_cmd(ssh, "ss -tlnp | grep 8000 || echo 'Port 8000 is free'")

print("\n[5] Remove any stale containers...")
run_cmd(ssh, "docker stop mindex-api 2>/dev/null; docker rm mindex-api 2>/dev/null; echo 'Cleaned'", show=False)

print("\n[6] Start container...")
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
    -e API_CORS_ORIGINS='["*"]' \
    mindex-api:latest 2>&1""")

print("\n[7] Waiting 15s...")
time.sleep(15)

print("\n[8] Check container...")
run_cmd(ssh, "docker ps --filter name=mindex-api")

print("\n[9] Container logs...")
run_cmd(ssh, "docker logs mindex-api --tail 20 2>&1")

print("\n[10] Test health...")
run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health")

ssh.close()
print("\n" + "=" * 70)
print("MINDEX RESTARTED!")
print("=" * 70)
