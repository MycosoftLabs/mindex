#!/usr/bin/env python3
"""Fix MINDEX DB connection - Feb 11, 2026"""

import os
import paramiko
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

VM_PASS = os.environ.get("VM_PASSWORD")
if not VM_PASS:
    print("ERROR: VM_PASSWORD not set")
    exit(1)

print("Connecting to MINDEX VM (192.168.0.189)...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)

print("\n1. Checking docker compose status...")
stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && docker compose ps", timeout=30)
print(stdout.read().decode())
err = stderr.read().decode()
if err:
    print("STDERR:", err)

print("\n2. Restarting mindex-postgres container...")
stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && docker compose restart mindex-postgres", timeout=60)
print(stdout.read().decode())
err = stderr.read().decode()
if err:
    print("STDERR:", err)

print("\n3. Waiting 8 seconds for postgres to initialize...")
time.sleep(8)

print("\n4. Restarting mindex-api container...")
stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && docker compose restart mindex-api", timeout=60)
print(stdout.read().decode())
err = stderr.read().decode()
if err:
    print("STDERR:", err)

print("\n5. Waiting 10 seconds for API to connect...")
time.sleep(10)

print("\n6. Verifying health endpoint...")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/api/mindex/health", timeout=30)
health_result = stdout.read().decode()
print(health_result)

print("\n7. Testing observations endpoint...")
stdin, stdout, stderr = ssh.exec_command("curl -s 'http://localhost:8000/api/mindex/observations?limit=3'", timeout=30)
obs_result = stdout.read().decode()
if obs_result:
    print(obs_result[:500] if len(obs_result) > 500 else obs_result)
else:
    print("No response")

print("\n8. Final container statuses...")
stdin, stdout, stderr = ssh.exec_command("docker ps --filter name=mindex --format 'table {{.Names}}\t{{.Status}}'", timeout=30)
print(stdout.read().decode())

print("\n9. Recent API logs (last 15 lines)...")
stdin, stdout, stderr = ssh.exec_command("docker logs mindex-api --tail 15 2>&1", timeout=30)
print(stdout.read().decode())

ssh.close()
print("\n✓ MINDEX database connection fix complete!")
