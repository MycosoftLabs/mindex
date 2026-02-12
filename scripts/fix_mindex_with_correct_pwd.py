#!/usr/bin/env python3
"""Fix MINDEX with correct password"""
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
    
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
    exit_code = stdout.channel.recv_exit_status()
    
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    
    if out:
        print(out)
    if err and "error" in err.lower():
        print(f"STDERR: {err}")
    
    return exit_code, out, err

print("="*70)
print("  MINDEX Database Fix")
print(f"  Target: {VM_HOST}")
print("="*70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

print(f"\n[*] Connecting to {VM_USER}@{VM_HOST}...")
try:
    ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
               look_for_keys=False, allow_agent=False)
    print("[OK] SSH connected!\n")
except Exception as e:
    print(f"[ERROR] SSH failed: {e}")
    sys.exit(1)

try:
    run_cmd(ssh, "docker ps --filter name=mindex --format '{{.Names}}: {{.Status}}'", 
            "Step 1: Container Status")
    
    run_cmd(ssh, "docker exec mindex-postgres psql -U mindex -d mindex -c '\\dt obs.*'", 
            "Step 2: Check Tables")
    
    code, out, err = run_cmd(ssh, "docker exec mindex-postgres psql -U mindex -d mindex -t -c 'SELECT COUNT(*) FROM core.taxon;'", 
            "Step 3: Taxon Count")
    taxon_count = out.strip()
    print(f"[INFO] Taxa count: {taxon_count}")
    
    code, out, err = run_cmd(ssh, "docker exec mindex-postgres psql -U mindex -d mindex -t -c 'SELECT COUNT(*) FROM obs.observation;'", 
            "Step 4: Observation Count")
    obs_count = out.strip()
    print(f"[INFO] Observation count: {obs_count}")
    
    # If no data, sync from GBIF
    if "0" in taxon_count or "does not exist" in str(out + err).lower():
        print("\n[ACTION] Database is empty - syncing data from GBIF...")
        print("This will take 2-5 minutes...")
        run_cmd(ssh, f"cd {MINDEX_DIR} && docker compose run --rm mindex-etl python -m mindex_etl.jobs.sync_gbif_taxa --limit 1000",
                "Step 5: Syncing GBIF Data")
    else:
        print("\n[SKIP] Database has data, skipping sync")
    
    run_cmd(ssh, f"cd {MINDEX_DIR} && docker compose restart mindex-api", 
            "Step 6: Restart API")
    print("\n[WAIT] Sleeping 10 seconds for API startup...")
    time.sleep(10)
    
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health", 
            "Step 7: Health Check")
    
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/stats", 
            "Step 8: Stats Test")
    
    run_cmd(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3" | head -200', 
            "Step 9: Observations Test")
    
except Exception as e:
    print(f"\n[ERROR] {e}")
finally:
    ssh.close()

print("\n" + "="*70)
print("  [DONE] Fix Complete")
print("="*70)
print("\nTest from Windows:")
print("  Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats")
print("  http://localhost:3010/natureos/mindex")
print("  http://localhost:3010/natureos/mindex/explorer")
