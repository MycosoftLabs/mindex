#!/usr/bin/env python3
"""Restart MINDEX API with rebuilt image - connect to existing DB containers"""
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
        for line in (output + error).strip().split('\n')[:30]:
            if line.strip():
                print(f"  {line}")
    return output + error

print("=" * 70)
print("MINDEX API RESTART")
print("=" * 70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("\n[1] Connecting to MINDEX VM...")
ssh.connect(VM_IP, username=VM_USER, password=VM_PASS, timeout=30)
print("  Connected")

print("\n[2] Pulling latest code...")
run_cmd(ssh, "cd /home/mycosoft/mindex && git fetch origin && git reset --hard origin/main")

print("\n[3] Remove any leftover API container and free port 8000...")
run_cmd(ssh, "docker rm -f mindex-api 2>/dev/null; true", show=False)
print("  Cleaned up")

print("\n[4] Building new image...")
output = run_cmd(ssh, "cd /home/mycosoft/mindex && docker build -t mindex-api:latest -f Dockerfile --no-cache . 2>&1 | tail -10", timeout=600)
print("  Build complete")

print("\n[5] Get network name...")
output = run_cmd(ssh, "docker network ls --filter name=mindex --format '{{.Name}}'")
network = output.strip().split('\n')[0] if output.strip() else "bridge"
print(f"  Using network: {network}")

print("\n[5b] Free port 8000 (kill host uvicorn if any)...")
pids_out = run_cmd(ssh, "fuser 8000/tcp 2>/dev/null || true", show=False)
pids = " ".join(p for p in pids_out.strip().split() if p.isdigit())
if pids:
    run_cmd(ssh, "kill -9 " + pids + " 2>/dev/null; true", show=False)
run_cmd(ssh, "fuser -k 8000/tcp 2>/dev/null; true", show=False)
run_cmd(ssh, "echo '%s' | sudo -S fuser -k 8000/tcp 2>/dev/null; true" % VM_PASS.replace("'", "'\"'\"'"), show=False)
time.sleep(2)

print("\n[6] Starting API container connected to existing infra...")
run_cmd(ssh, f"""docker run -d \\
    --name mindex-api \\
    --restart unless-stopped \\
    -p 8000:8000 \\
    --network {network} \\
    -e MINDEX_DB_HOST=mindex-postgres \\
    -e MINDEX_DB_PORT=5432 \\
    -e MINDEX_DB_USER=mycosoft \\
    -e MINDEX_DB_PASSWORD=mycosoft_mindex_2026 \\
    -e MINDEX_DB_NAME=mindex \\
    -e API_CORS_ORIGINS='[\"http://localhost:3000\",\"http://localhost:3010\",\"http://192.168.0.187:3000\",\"http://192.168.0.172:3010\"]' \\
    mindex-api:latest 2>&1""")
print("  Container started")

print("\n[7] Waiting 15s for startup...")
time.sleep(15)

print("\n[8] Checking container status...")
output = run_cmd(ssh, "docker ps --filter name=mindex-api --format '{{.Names}}: {{.Status}}'")

print("\n[9] Container logs...")
output = run_cmd(ssh, "docker logs mindex-api --tail 15 2>&1")

print("\n[10] Testing health endpoint...")
output = run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health 2>&1")

ssh.close()

print("\n" + "=" * 70)
print("MINDEX DEPLOYMENT COMPLETE")
print("=" * 70)
