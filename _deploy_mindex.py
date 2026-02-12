#!/usr/bin/env python3
"""Deploy MINDEX to VM 189 - Feb 11, 2026"""

import os
import paramiko
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = os.environ.get("VM_PASSWORD")
MINDEX_DIR = "/home/mycosoft/mindex"

if not VM_PASS:
    print("ERROR: VM_PASSWORD environment variable is not set.")
    print("Please set it with: $env:VM_PASSWORD = 'your-password'")
    sys.exit(1)

def main():
    print(f"Connecting to {VM_HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30)
    
    print("\n1. Pulling latest code...")
    stdin, stdout, stderr = ssh.exec_command(f"cd {MINDEX_DIR} && git fetch origin && git reset --hard origin/main", timeout=60)
    print(stdout.read().decode())
    
    print("\n2. Checking for Dockerfile...")
    stdin, stdout, stderr = ssh.exec_command(f"ls -la {MINDEX_DIR}/Dockerfile* 2>/dev/null || echo 'No Dockerfile found'", timeout=30)
    print(stdout.read().decode())
    
    print("\n3. Rebuilding API image and bringing up...")
    stdin, stdout, stderr = ssh.exec_command(
        f"cd {MINDEX_DIR} && docker-compose build --no-cache api 2>&1",
        timeout=300
    )
    out, err = stdout.read().decode(), stderr.read().decode()
    print(out)
    if err:
        print(f"stderr: {err}")
    stdin, stdout, stderr = ssh.exec_command(f"cd {MINDEX_DIR} && docker-compose up -d --no-recreate api 2>&1", timeout=120)
    out, err = stdout.read().decode(), stderr.read().decode()
    print(out)
    if err:
        print(f"stderr: {err}")
    # If compose failed, try restarting existing container (bind-mounted code already updated by git pull)
    if "Error" in out or "error" in err.lower():
        print("\n3b. Trying docker restart mindex-api (code already updated via bind mount)...")
        stdin, stdout, stderr = ssh.exec_command("docker restart mindex-api 2>&1", timeout=60)
        print(stdout.read().decode())
    time.sleep(5)

    print("\n5. Checking container status...")
    stdin, stdout, stderr = ssh.exec_command("docker ps --format '{{.Names}}: {{.Status}}' | grep -i mindex", timeout=30)
    print(stdout.read().decode())
    
    print("\n6. Checking what's running on port 8000...")
    stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/ | head -10", timeout=30)
    result = stdout.read().decode()
    print(result if result else "No response on port 8000")
    
    print("\n7. Checking mindex-api logs...")
    stdin, stdout, stderr = ssh.exec_command("docker logs mindex-mindex-api-1 --tail 20 2>&1 || docker logs mindex-api --tail 20 2>&1", timeout=30)
    print(stdout.read().decode())
    
    ssh.close()
    print("\n✅ MINDEX deployment complete!")

if __name__ == "__main__":
    main()
