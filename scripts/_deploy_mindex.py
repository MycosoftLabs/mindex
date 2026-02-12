#!/usr/bin/env python3
"""Deploy MINDEX to VM 192.168.0.189"""
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

print("\n[3] Restarting MINDEX API container...")
run_sudo(ssh, "cd /home/mycosoft/mindex && docker compose stop mindex-api 2>/dev/null || true", VM_PASS, show=False)
run_sudo(ssh, "cd /home/mycosoft/mindex && docker compose build --no-cache mindex-api 2>&1 | tail -10", VM_PASS, timeout=300)
run_sudo(ssh, "cd /home/mycosoft/mindex && docker compose up -d mindex-api", VM_PASS)
print("  Container restarted")

print("\n[4] Waiting 10s for startup...")
time.sleep(10)

print("\n[5] Checking API status...")
output = run_cmd(ssh, "curl -s http://localhost:8000/health 2>&1 || echo 'API check failed'")

ssh.close()

print("\n[6] Testing API from local machine...")
import urllib.request
try:
    with urllib.request.urlopen(f"http://{VM_IP}:8000/health", timeout=10) as resp:
        data = resp.read().decode()
        print(f"  Response: {data[:200]}")
except Exception as e:
    print(f"  API check failed: {e}")

print("\n" + "=" * 70)
print("MINDEX DEPLOYMENT COMPLETE")
print("=" * 70)
print(f"""
MINDEX VM: {VM_IP}
API: http://{VM_IP}:8000/health
Docs: http://{VM_IP}:8000/docs
""")
