#!/usr/bin/env python3
"""Full MINDEX fix - kill all, verify port free, then start"""
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

def run_sudo(ssh, cmd, password, timeout=120):
    full_cmd = f"echo '{password}' | sudo -S {cmd}"
    stdin, stdout, stderr = ssh.exec_command(full_cmd, timeout=timeout, get_pty=True)
    return stdout.read().decode('utf-8', errors='ignore')

print("=" * 70)
print("FULL MINDEX FIX")
print("=" * 70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("\n[1] Connecting...")
ssh.connect(VM_IP, username=VM_USER, password=VM_PASS, timeout=30)

print("\n[2] What's on port 8000?")
run_cmd(ssh, "ss -tlnp | grep 8000")

print("\n[3] Kill ALL uvicorn and python processes that might be using 8000...")
run_sudo(ssh, "fuser -k 8000/tcp 2>/dev/null || true", VM_PASS)
run_sudo(ssh, "pkill -9 -f uvicorn 2>/dev/null || true", VM_PASS)
run_sudo(ssh, "pkill -9 -f 'api:app' 2>/dev/null || true", VM_PASS)
time.sleep(3)
print("  Killed")

print("\n[4] Remove any Docker containers...")
run_cmd(ssh, "docker stop mindex-api 2>/dev/null || true", show=False)
run_cmd(ssh, "docker rm mindex-api 2>/dev/null || true", show=False)
print("  Removed")

print("\n[5] Verify port 8000 is free...")
output = run_cmd(ssh, "ss -tlnp | grep 8000 || echo 'PORT 8000 IS FREE'")
if "8000 IS FREE" not in output:
    print("  WARNING: Port still in use, trying again...")
    run_sudo(ssh, "fuser -k 8000/tcp", VM_PASS)
    time.sleep(2)
    run_cmd(ssh, "ss -tlnp | grep 8000 || echo 'PORT 8000 IS FREE'")

print("\n[6] Start MINDEX container...")
result = run_cmd(ssh, """docker run -d \
    --name mindex-api \
    --restart unless-stopped \
    -p 8000:8000 \
    --network mindex_mindex-network \
    -e MINDEX_DB_HOST=mindex-postgres \
    -e MINDEX_DB_PORT=5432 \
    -e MINDEX_DB_USER=mycosoft \
    -e MINDEX_DB_PASSWORD=mycosoft_mindex_2026 \
    -e MINDEX_DB_NAME=mindex \
    -e 'API_CORS_ORIGINS=["http://localhost:3000","http://localhost:3010","http://192.168.0.187:3000"]' \
    mindex-api:latest 2>&1""")

if "Error" in result:
    print("  FAILED - trying without network restriction...")
    run_cmd(ssh, "docker rm -f mindex-api 2>/dev/null || true", show=False)
    run_cmd(ssh, """docker run -d \
        --name mindex-api \
        --restart unless-stopped \
        -p 8000:8000 \
        -e MINDEX_DB_HOST=192.168.0.189 \
        -e MINDEX_DB_PORT=5432 \
        -e MINDEX_DB_USER=mycosoft \
        -e MINDEX_DB_PASSWORD=mycosoft_mindex_2026 \
        -e MINDEX_DB_NAME=mindex \
        -e 'API_CORS_ORIGINS=["http://localhost:3000","http://localhost:3010"]' \
        mindex-api:latest 2>&1""")

print("\n[7] Waiting 15s...")
time.sleep(15)

print("\n[8] Check container...")
run_cmd(ssh, "docker ps --filter name=mindex-api")

print("\n[9] Logs...")
run_cmd(ssh, "docker logs mindex-api --tail 20 2>&1")

print("\n[10] Test health...")
run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health")

ssh.close()
print("\n" + "=" * 70 + "\nDONE\n" + "=" * 70)
