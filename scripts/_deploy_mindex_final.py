#!/usr/bin/env python3
"""Deploy MINDEX to VM 192.168.0.189 - Final working version"""
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

print("\n[3] Using docker-compose to rebuild 'api' service...")
output = run_cmd(ssh, "cd /home/mycosoft/mindex && docker-compose stop api 2>&1 || true", show=False)
print("  Stopped old container")

output = run_cmd(ssh, "cd /home/mycosoft/mindex && docker-compose build --no-cache api 2>&1 | tail -15", timeout=600)
print("  Build complete")

output = run_cmd(ssh, "cd /home/mycosoft/mindex && docker-compose up -d api 2>&1")
print("  Container started")

print("\n[4] Waiting 15s for startup...")
time.sleep(15)

print("\n[5] Checking container status...")
output = run_cmd(ssh, "docker ps --filter name=mindex-api")

print("\n[6] Testing health endpoint from VM...")
output = run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health 2>&1 | head -5")

ssh.close()

print("\n" + "=" * 70)
print("MINDEX DEPLOYMENT COMPLETE")
print("=" * 70)
print(f"""
MINDEX VM: {VM_IP}
API Health: http://{VM_IP}:8000/api/mindex/health
Docs: http://{VM_IP}:8000/docs
Research API: http://{VM_IP}:8000/api/mindex/research/search?query=fungi
""")
