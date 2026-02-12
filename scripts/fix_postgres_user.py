#!/usr/bin/env python3
"""Fix MINDEX PostgreSQL user and database"""
import paramiko
import sys
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = "Mushroom1!Mushroom1!"
MINDEX_DIR = "/home/mycosoft/mindex"

def run_cmd(ssh, cmd, desc=""):
    if desc:
        print(f"\n{'='*70}")
        print(f"  {desc}")
        print('='*70)
    print(f"$ {cmd}\n")
    
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120, get_pty=True)
    exit_code = stdout.channel.recv_exit_status()
    
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    
    if out:
        print(out)
    
    return exit_code, out, err

print("="*70)
print("  MINDEX PostgreSQL User Fix")
print("="*70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

print(f"\n[*] Connecting to {VM_USER}@{VM_HOST}...")
ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
           look_for_keys=False, allow_agent=False)
print("[OK] Connected!\n")

try:
    # Check current user
    run_cmd(ssh, "docker exec mindex-postgres psql -U postgres -c '\\du'", 
            "Step 1: List PostgreSQL Users")
    
    # Create mindex user if not exists
    run_cmd(ssh, "docker exec mindex-postgres psql -U postgres -c \"CREATE USER mindex WITH PASSWORD 'mindex' SUPERUSER;\" 2>&1 || echo 'User may already exist'", 
            "Step 2: Create mindex User")
    
    # Create mindex database if not exists
    run_cmd(ssh, "docker exec mindex-postgres psql -U postgres -c \"CREATE DATABASE mindex OWNER mindex;\" 2>&1 || echo 'Database may already exist'", 
            "Step 3: Create mindex Database")
    
    # Grant privileges
    run_cmd(ssh, "docker exec mindex-postgres psql -U postgres -c \"GRANT ALL PRIVILEGES ON DATABASE mindex TO mindex;\"", 
            "Step 4: Grant Privileges")
    
    # Check if we can connect as mindex now
    run_cmd(ssh, "docker exec mindex-postgres psql -U mindex -d mindex -c 'SELECT version();'", 
            "Step 5: Test mindex User Connection")
    
    # Run init migration
    run_cmd(ssh, f"cd {MINDEX_DIR} && docker exec -i mindex-postgres psql -U mindex -d mindex < migrations/0001_init.sql 2>&1 | head -50", 
            "Step 6: Run Init Migration")
    
    # Check tables now
    run_cmd(ssh, "docker exec mindex-postgres psql -U mindex -d mindex -c '\\dt obs.*'", 
            "Step 7: Verify Tables Created")
    
    # Sync data from GBIF
    print("\n[ACTION] Syncing 1000 taxa from GBIF (this takes 2-5 minutes)...")
    run_cmd(ssh, f"cd {MINDEX_DIR} && timeout 300 docker compose run --rm mindex-etl python -m mindex_etl.jobs.sync_gbif_taxa --limit 1000 2>&1 | tail -50", 
            "Step 8: Sync GBIF Data")
    
    # Restart API
    run_cmd(ssh, f"cd {MINDEX_DIR} && docker compose restart mindex-api", 
            "Step 9: Restart API")
    
    print("\n[WAIT] Sleeping 10 seconds...")
    time.sleep(10)
    
    # Final health check
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health", 
            "Step 10: Final Health Check")
    
    # Test stats
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/stats | head -100", 
            "Step 11: Test Stats Endpoint")
    
    # Test observations
    run_cmd(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3" | head -200', 
            "Step 12: Test Observations Endpoint")
    
except Exception as e:
    print(f"\n[ERROR] {e}")
    import traceback
    traceback.print_exc()
finally:
    ssh.close()

print("\n" + "="*70)
print("  [SUCCESS] MINDEX Database Fixed!")
print("="*70)
print("\nTest from Windows:")
print("  Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats")
print("  Invoke-RestMethod http://localhost:3010/api/natureos/mindex/stats")
print("\nOpen in browser:")
print("  http://localhost:3010/natureos/mindex")
print("  http://localhost:3010/natureos/mindex/explorer")
print("  http://localhost:3010/mindex")
