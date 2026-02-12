#!/usr/bin/env python3
"""
Fix MINDEX Database Connection - Feb 11, 2026
Restarts PostgreSQL and MINDEX API containers on VM 192.168.0.189
"""

import os
import paramiko
import sys
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = os.environ.get("VM_PASSWORD")
MINDEX_DIR = "/home/mycosoft/mindex"

if not VM_PASS:
    print("ERROR: VM_PASSWORD environment variable not set")
    print("Set it with: $env:VM_PASSWORD = 'Mycosoft2024!'")
    print("Or check your VM password file")
    sys.exit(1)

def run_cmd(ssh, cmd, desc=""):
    """Execute command and print output"""
    if desc:
        print(f"\n{'='*60}")
        print(f"  {desc}")
        print('='*60)
    print(f"$ {cmd}\n")
    
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
    exit_code = stdout.channel.recv_exit_status()
    
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    
    if out:
        print(out)
    if err:
        print(f"STDERR: {err}")
    
    return exit_code, out, err

def main():
    print("="*60)
    print("  MINDEX Database Fix Script")
    print(f"  Target: {VM_HOST}")
    print("="*60)
    
    # Connect
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    print(f"\n[*] Connecting to {VM_USER}@{VM_HOST}...")
    try:
        # Try SSH key first, then password
        ssh.connect(
            VM_HOST, 
            username=VM_USER, 
            password=VM_PASS, 
            key_filename=os.path.expanduser("~/.ssh/id_rsa"),
            look_for_keys=True,
            allow_agent=True,
            timeout=30
        )
        print("[OK] SSH connection established!\n")
    except Exception as e:
        print(f"[ERROR] SSH connection failed: {e}")
        print(f"[INFO] Tried password auth and SSH key ~/.ssh/id_rsa")
        print(f"[FIX] Ensure VM password is correct or SSH key is configured")
        sys.exit(1)
    
    # Check current status
    run_cmd(ssh, "docker ps --filter name=mindex --format '{{.Names}}: {{.Status}}'", 
            "Step 1: Current Container Status")
    
    # Check what's on port 8000
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health 2>&1 | python3 -m json.tool || curl -s http://localhost:8000/api/mindex/health", 
            "Step 2: Current API Health")
    
    # Check PostgreSQL specifically
    run_cmd(ssh, "docker logs mindex-postgres --tail 10 2>&1 || docker logs mindex-mindex-postgres-1 --tail 10 2>&1", 
            "Step 3: PostgreSQL Logs")
    
    # Restart PostgreSQL
    print("\n" + "="*60)
    print("  Step 4: Restarting PostgreSQL...")
    print("="*60)
    run_cmd(ssh, f"cd {MINDEX_DIR} && docker compose restart mindex-postgres")
    print("[WAIT] Waiting 8 seconds for PostgreSQL startup...")
    time.sleep(8)
    
    # Restart Redis
    run_cmd(ssh, f"cd {MINDEX_DIR} && docker compose restart mindex-redis", 
            "Step 5: Restarting Redis")
    time.sleep(3)
    
    # Restart Qdrant
    run_cmd(ssh, f"cd {MINDEX_DIR} && docker compose restart mindex-qdrant", 
            "Step 6: Restarting Qdrant")
    time.sleep(3)
    
    # Restart MINDEX API
    run_cmd(ssh, f"cd {MINDEX_DIR} && docker compose restart mindex-api", 
            "Step 7: Restarting MINDEX API")
    print("[WAIT] Waiting 10 seconds for API startup...")
    time.sleep(10)
    
    # Check final status
    run_cmd(ssh, "docker ps --filter name=mindex --format '{{.Names}}: {{.Status}}'", 
            "Step 8: Final Container Status")
    
    # Check health
    exit_code, out, err = run_cmd(ssh, 
            "curl -s http://localhost:8000/api/mindex/health 2>&1", 
            "Step 9: API Health Check")
    
    if "ok" in out and '"db":' in out:
        if '"db": "ok"' in out or '"db":"ok"' in out:
            print("\n[SUCCESS] Database connection RESTORED!")
        else:
            print("\n[WARNING] API is up but database still showing error")
    
    # Test observations endpoint
    exit_code, out, err = run_cmd(ssh, 
            'curl -s "http://localhost:8000/api/mindex/observations?limit=3" 2>&1 | head -50', 
            "Step 10: Test Observations Endpoint")
    
    # Check API logs for errors
    run_cmd(ssh, "docker logs mindex-api --tail 20 2>&1 || docker logs mindex-mindex-api-1 --tail 20 2>&1", 
            "Step 11: Recent API Logs")
    
    ssh.close()
    
    print("\n" + "="*60)
    print("  [DONE] RESTART COMPLETE")
    print("="*60)
    print("\nNext Steps:")
    print("  1. Verify from local machine:")
    print("     Invoke-RestMethod http://192.168.0.189:8000/api/mindex/health")
    print("\n  2. Test website integration:")
    print("     http://localhost:3010/natureos/mindex")
    print("     http://localhost:3010/natureos/mindex/explorer")
    print("\n  3. Check if data pipeline shows 'online'")
    
if __name__ == "__main__":
    main()
