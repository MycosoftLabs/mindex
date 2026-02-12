#!/usr/bin/env python3
"""Deploy MINDEX to VM 192.168.0.189 - Using docker-compose"""
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

print("\n[3] Check docker networks...")
output = run_cmd(ssh, "docker network ls | grep mindex")

print("\n[4] Using docker-compose to rebuild and restart...")
output = run_cmd(ssh, "cd /home/mycosoft/mindex && docker-compose stop mindex-api 2>&1 || true", show=False)
print("  Stopped old container")

output = run_cmd(ssh, "cd /home/mycosoft/mindex && docker-compose build --no-cache mindex-api 2>&1 | tail -10", timeout=600)
print("  Build complete")

output = run_cmd(ssh, "cd /home/mycosoft/mindex && docker-compose up -d mindex-api 2>&1")
print("  Container started")

print("\n[5] Waiting 15s for startup...")
time.sleep(15)

print("\n[6] Checking container status...")
output = run_cmd(ssh, "docker ps --filter name=mindex | head -5")

print("\n[7] Testing health endpoint from VM...")
output = run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health 2>&1 | head -5")

ssh.close()

print("\n" + "=" * 70)
print("MINDEX DEPLOYMENT COMPLETE")
print("=" * 70)
print(f"""
MINDEX VM: {VM_IP}
API Health: http://{VM_IP}:8000/api/mindex/health
Docs: http://{VM_IP}:8000/docs
""")
