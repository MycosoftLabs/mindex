#!/usr/bin/env python3
"""Deploy MINDEX to VM 192.168.0.189 - Fixed version"""
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
print("MINDEX DEPLOYMENT TO VM 192.168.0.189")
print("=" * 70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print("\n[1] Connecting to MINDEX VM...")
ssh.connect(VM_IP, username=VM_USER, password=VM_PASS, timeout=30)
print("  Connected")

print("\n[2] Pulling latest code from GitHub...")
output = run_cmd(ssh, "cd /home/mycosoft/mindex && git fetch origin && git reset --hard origin/main")
print("  Code updated")

print("\n[3] Stopping MINDEX API container...")
output = run_cmd(ssh, "docker stop mindex-api 2>/dev/null || true", show=False)
output = run_cmd(ssh, "docker rm mindex-api 2>/dev/null || true", show=False)
print("  Container stopped")

print("\n[4] Rebuilding Docker image (this takes ~2 minutes)...")
output = run_cmd(ssh, "cd /home/mycosoft/mindex && docker build -t mindex-api:latest -f Dockerfile --no-cache . 2>&1 | tail -15", timeout=600)
print("  Build complete")

print("\n[5] Starting container...")
run_cmd(ssh, """docker run -d \
    --name mindex-api \
    --restart unless-stopped \
    -p 8000:8000 \
    --network mindex_default \
    -e DATABASE_URL=postgresql://mycosoft:mycosoft_mindex_2026@mindex-postgres:5432/mindex \
    -e REDIS_URL=redis://mindex-redis:6379/0 \
    -e QDRANT_URL=http://mindex-qdrant:6333 \
    mindex-api:latest 2>&1""")
print("  Container started")

print("\n[6] Waiting 10s for startup...")
time.sleep(10)

print("\n[7] Checking container status...")
output = run_cmd(ssh, "docker ps --filter name=mindex-api --format '{{.Names}}: {{.Status}}'")

print("\n[8] Testing health endpoint from VM...")
output = run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health")

ssh.close()

print("\n" + "=" * 70)
print("MINDEX DEPLOYMENT COMPLETE")
print("=" * 70)
print(f"""
MINDEX VM: {VM_IP}
API Health: http://{VM_IP}:8000/api/mindex/health
Docs: http://{VM_IP}:8000/docs
""")
