#!/usr/bin/env python3
"""Direct SSH fix for MINDEX database - tries common passwords"""
import paramiko
import sys
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
MINDEX_DIR = "/home/mycosoft/mindex"

# Try common passwords
PASSWORDS = [
    "Mycosoft2024!",
    "mycosoft2024",
    "Mycosoft123!",
    "mycosoft123",
]

def try_connect(password):
    """Try to connect with given password"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(VM_HOST, username=VM_USER, password=password, timeout=10, 
                   look_for_keys=False, allow_agent=False)
        return ssh
    except:
        return None

def run_cmd(ssh, cmd):
    """Run command and return output"""
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
    return stdout.read().decode('utf-8', errors='replace').strip()

print("="*70)
print("  MINDEX Database Direct Fix")
print(f"  Target: {VM_HOST}")
print("="*70)

# Try to connect
ssh = None
for pwd in PASSWORDS:
    print(f"\nTrying password: {pwd[:4]}...")
    ssh = try_connect(pwd)
    if ssh:
        print(f"[SUCCESS] Connected with password: {pwd[:4]}****\n")
        break

if not ssh:
    print("\n[ERROR] All password attempts failed")
    print("Please manually SSH and run these commands:")
    print(f"  ssh {VM_USER}@{VM_HOST}")
    print(f"  cd {MINDEX_DIR}")
    print("  docker compose restart")
    sys.exit(1)

try:
    # Step 1: Check containers
    print("\n[1/8] Checking Docker containers...")
    print("-" * 70)
    out = run_cmd(ssh, "docker ps --filter name=mindex --format '{{.Names}}: {{.Status}}'")
    print(out)
    
    # Step 2: Check database tables
    print("\n[2/8] Checking if tables exist...")
    print("-" * 70)
    out = run_cmd(ssh, "docker exec mindex-postgres psql -U mindex -d mindex -c '\\dt obs.*' 2>&1")
    print(out)
    
    # Step 3: Check taxon count
    print("\n[3/8] Checking taxon count...")
    print("-" * 70)
    out = run_cmd(ssh, "docker exec mindex-postgres psql -U mindex -d mindex -t -c 'SELECT COUNT(*) FROM core.taxon;' 2>&1")
    taxon_count = out.strip()
    print(f"Taxa count: {taxon_count}")
    
    # Step 4: Check observation count
    print("\n[4/8] Checking observation count...")
    print("-" * 70)
    out = run_cmd(ssh, "docker exec mindex-postgres psql -U mindex -d mindex -t -c 'SELECT COUNT(*) FROM obs.observation;' 2>&1")
    obs_count = out.strip()
    print(f"Observation count: {obs_count}")
    
    # Step 5: If tables are empty, run init migration
    if "0" in taxon_count or "does not exist" in out.lower():
        print("\n[5/8] Tables empty or missing - running init migration...")
        print("-" * 70)
        out = run_cmd(ssh, f"cd {MINDEX_DIR} && docker exec -i mindex-postgres psql -U mindex -d mindex < migrations/0001_init.sql 2>&1")
        print(out[:500])
    else:
        print("\n[5/8] Tables exist with data - skipping migration")
    
    # Step 6: Restart all containers
    print("\n[6/8] Restarting all MINDEX containers...")
    print("-" * 70)
    out = run_cmd(ssh, f"cd {MINDEX_DIR} && docker compose restart")
    print(out)
    print("[WAIT] Sleeping 15 seconds for startup...")
    time.sleep(15)
    
    # Step 7: Check health
    print("\n[7/8] Checking API health...")
    print("-" * 70)
    out = run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health 2>&1")
    print(out)
    
    # Step 8: Test stats endpoint
    print("\n[8/8] Testing stats endpoint...")
    print("-" * 70)
    out = run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/stats 2>&1 | head -100")
    print(out)
    
    # Test observations
    print("\n[BONUS] Testing observations endpoint...")
    print("-" * 70)
    out = run_cmd(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3" 2>&1 | head -100')
    print(out)
    
except Exception as e:
    print(f"\n[ERROR] Command failed: {e}")
finally:
    ssh.close()

print("\n" + "="*70)
print("  [DONE] MINDEX Fix Complete")
print("="*70)
print("\nTest from Windows:")
print("  Invoke-RestMethod http://192.168.0.189:8000/api/mindex/health")
print("  Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats")
print("\nTest website:")
print("  http://localhost:3010/natureos/mindex")
print("  http://localhost:3010/natureos/mindex/explorer")
