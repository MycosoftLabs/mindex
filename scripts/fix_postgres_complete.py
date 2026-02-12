#!/usr/bin/env python3
"""Complete PostgreSQL fix for MINDEX"""
import paramiko
import sys
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = "Mushroom1!Mushroom1!"
MINDEX_DIR = "/home/mycosoft/mindex"

def run_cmd(ssh, cmd, desc="", timeout=120):
    if desc:
        print(f"\n{'='*70}")
        print(f"  {desc}")
        print('='*70)
    print(f"$ {cmd}\n")
    
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout, get_pty=True)
    exit_code = stdout.channel.recv_exit_status()
    
    out = stdout.read().decode('utf-8', errors='replace').strip()
    
    if out:
        print(out)
    
    return exit_code, out

print("="*70)
print("  MINDEX Complete Database Fix")
print("="*70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

print(f"\n[*] Connecting to {VM_USER}@{VM_HOST}...")
ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
           look_for_keys=False, allow_agent=False)
print("[OK] Connected!\n")

try:
    # Check docker-compose version
    code, out = run_cmd(ssh, "docker-compose --version 2>&1 || docker compose version 2>&1", 
            "Step 1: Docker Compose Version")
    
    # Use correct docker-compose command
    if "docker-compose" in out.lower() and "version" in out.lower():
        DC = "docker-compose"
    else:
        DC = "docker compose"
    print(f"[INFO] Using command: {DC}\n")
    
    # Check containers
    run_cmd(ssh, f"cd {MINDEX_DIR} && {DC} ps", 
            "Step 2: Container Status")
    
    # Check PostgreSQL env vars
    run_cmd(ssh, f"cd {MINDEX_DIR} && cat .env | grep -E 'POSTGRES|DB' || echo 'No .env file'", 
            "Step 3: Database Configuration")
    
    # Stop and remove postgres to rebuild
    print("\n[ACTION] Rebuilding PostgreSQL container...")
    run_cmd(ssh, f"cd {MINDEX_DIR} && {DC} stop mindex-postgres", "Step 4a: Stop PostgreSQL")
    run_cmd(ssh, f"cd {MINDEX_DIR} && {DC} rm -f mindex-postgres", "Step 4b: Remove Container")
    
    # Recreate with proper initialization
    run_cmd(ssh, f"cd {MINDEX_DIR} && {DC} up -d mindex-postgres", 
            "Step 5: Start PostgreSQL Fresh")
    
    print("\n[WAIT] Sleeping 15 seconds for PostgreSQL initialization...")
    time.sleep(15)
    
    # Check logs
    run_cmd(ssh, f"{DC} logs mindex-postgres --tail 30", 
            "Step 6: PostgreSQL Startup Logs")
    
    # Find what user postgres is using
    code, out = run_cmd(ssh, "docker exec mindex-postgres env | grep POSTGRES", 
            "Step 7: PostgreSQL Environment")
    
    # Try to connect with default user
    run_cmd(ssh, "docker exec mindex-postgres psql --version", 
            "Step 8: PostgreSQL Version")
    
    # List databases with whatever user works
    run_cmd(ssh, "docker exec mindex-postgres psql -l 2>&1 || docker exec mindex-postgres psql -U \$POSTGRES_USER -l 2>&1", 
            "Step 9: List Databases")
    
    # Check docker-compose.yml to see what user it's configured with
    code, out = run_cmd(ssh, f"cd {MINDEX_DIR} && cat docker-compose.yml | grep -A 10 'mindex-postgres' | grep -E 'POSTGRES_USER|POSTGRES_PASSWORD|POSTGRES_DB'", 
            "Step 10: Docker Compose PostgreSQL Config")
    
    # Restart API
    run_cmd(ssh, f"cd {MINDEX_DIR} && {DC} restart mindex-api", 
            "Step 11: Restart MINDEX API")
    
    print("\n[WAIT] Sleeping 10 seconds...")
    time.sleep(10)
    
    # Test health
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health", 
            "Step 12: Health Check")
    
    # Test stats
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/stats 2>&1 | head -100", 
            "Step 13: Stats Test")
    
except Exception as e:
    print(f"\n[ERROR] {e}")
    import traceback
    traceback.print_exc()
finally:
    ssh.close()

print("\n" + "="*70)
print("  Fix attempt complete - check output above")
print("="*70)
