#!/usr/bin/env python3
"""Fix MINDEX deployment by cleaning up and restarting properly"""
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
print("FIX MINDEX DEPLOYMENT")
print("=" * 70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("\n[1] Connecting...")
ssh.connect(VM_IP, username=VM_USER, password=VM_PASS, timeout=30)

print("\n[2] What's using port 8000?")
run_cmd(ssh, "docker ps --format '{{.Names}}: {{.Ports}}' | grep 8000 || echo 'No docker container on 8000'")
run_cmd(ssh, "netstat -tlnp 2>/dev/null | grep 8000 || ss -tlnp | grep 8000 || echo 'No process on 8000'")

print("\n[3] All running containers...")
run_cmd(ssh, "docker ps --format '{{.Names}}: {{.Image}} ({{.Status}})'")

print("\n[4] Stop ALL containers using port 8000...")
# Find container IDs using port 8000
output = run_cmd(ssh, "docker ps --format '{{.ID}}' -f publish=8000", show=False)
containers = [c.strip() for c in output.strip().split('\n') if c.strip()]
for cid in containers:
    print(f"  Stopping {cid}...")
    run_cmd(ssh, f"docker stop {cid} && docker rm {cid}", show=False)

# Also try by name
run_cmd(ssh, "docker stop mindex-api 2>/dev/null || true", show=False)
run_cmd(ssh, "docker rm mindex-api 2>/dev/null || true", show=False)
print("  Cleaned up")

print("\n[5] Pull latest code...")
run_cmd(ssh, "cd /home/mycosoft/mindex && git fetch origin && git reset --hard origin/main", show=False)
print("  Done")

print("\n[6] Build image...")
output = run_cmd(ssh, "cd /home/mycosoft/mindex && docker build -t mindex-api:latest . 2>&1 | tail -5", timeout=300)

print("\n[7] Start container on mindex_mindex-network...")
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

print("\n[8] Waiting 10s...")
time.sleep(10)

print("\n[9] Check container...")
run_cmd(ssh, "docker ps --filter name=mindex-api")

print("\n[10] Check logs...")
run_cmd(ssh, "docker logs mindex-api --tail 20 2>&1")

print("\n[11] Test health...")
run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health")

ssh.close()
print("\n" + "=" * 70 + "\nDONE\n" + "=" * 70)
