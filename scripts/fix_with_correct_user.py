#!/usr/bin/env python3
"""Fix MINDEX with correct PostgreSQL user: mycosoft"""
import paramiko
import sys
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = "Mushroom1!Mushroom1!"
MINDEX_DIR = "/home/mycosoft/mindex"
PG_USER = "mycosoft"  # Correct PostgreSQL user
PG_DB = "mindex"

def run_cmd(ssh, cmd, desc="", timeout=180):
    if desc:
        print(f"\n{'='*70}")
        print(f"  {desc}")
        print('='*70)
    print(f"$ {cmd}\n")
    
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout, get_pty=True)
    exit_code = stdout.channel.recv_exit_status()
    
    out = stdout.read().decode('utf-8', errors='replace').strip()
    
    if out:
        # Clean ANSI codes for readability
        import re
        out = re.sub(r'\x1b\[[0-9;]*m', '', out)
        print(out)
    
    return exit_code, out

print("="*70)
print("  MINDEX Database Fix - Using Correct User")
print("="*70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

print(f"\n[*] Connecting...")
ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
           look_for_keys=False, allow_agent=False)
print("[OK] Connected!\n")

try:
    # List databases with correct user
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dt obs.*'", 
            "Step 1: Check Tables (with user mycosoft)")
    
    # Check counts
    code, out = run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -t -c 'SELECT COUNT(*) FROM core.taxon;'", 
            "Step 2: Count Taxa")
    taxon_count = out.strip().split()[-1] if out else "0"
    print(f"[INFO] Taxa: {taxon_count}")
    
    code, out = run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -t -c 'SELECT COUNT(*) FROM obs.observation;'", 
            "Step 3: Count Observations")
    obs_count = out.strip().split()[-1] if out else "0"
    print(f"[INFO] Observations: {obs_count}")
    
    # If tables are empty or don't exist, run migration
    if taxon_count == "0" or "does not exist" in out.lower():
        print("\n[ACTION] Running migrations to create/initialize schema...")
        run_cmd(ssh, f"cd {MINDEX_DIR} && docker exec -i mindex-postgres psql -U {PG_USER} -d {PG_DB} < migrations/0001_init.sql 2>&1 | tail -30", 
                "Step 4: Initialize Schema")
        
        # Verify tables created
        run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dt obs.*'", 
                "Step 5: Verify Tables")
    
    # Check if we need data
    code, out = run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -t -c 'SELECT COUNT(*) FROM core.taxon;'", 
            "Step 6: Recheck Taxa Count")
    new_count = out.strip().split()[-1] if out else "0"
    
    if new_count == "0" or int(new_count) < 10:
        print(f"\n[ACTION] Database has {new_count} taxa - syncing from GBIF...")
        print("[INFO] This takes 2-5 minutes for 1000 records...")
        
        # Find correct compose file location
        code, out = run_cmd(ssh, "find /home/mycosoft -name 'docker-compose.yml' -path '*/mindex/*' 2>/dev/null", 
                "Finding docker-compose.yml")
        
        compose_dir = "/home/mycosoft/mindex"
        if out:
            compose_dir = out.split()[0].replace('/docker-compose.yml', '')
            print(f"[INFO] Found compose file in: {compose_dir}")
        
        # Run ETL sync
        run_cmd(ssh, f"cd {compose_dir} && docker-compose run --rm mindex-etl python -m mindex_etl.jobs.sync_gbif_taxa --limit 1000 2>&1 | tail -100", 
                "Step 7: Sync GBIF Data", timeout=300)
    else:
        print(f"\n[SKIP] Database has {new_count} taxa - no sync needed")
    
    # Restart mindex-api container
    run_cmd(ssh, "docker restart mindex-api 2>&1", 
            "Step 8: Restart API Container")
    
    print("\n[WAIT] Sleeping 10 seconds...")
    time.sleep(10)
    
    # Final tests
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health", 
            "Step 9: Health Check")
    
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/stats | head -200", 
            "Step 10: Stats Endpoint")
    
    run_cmd(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3" | head -200', 
            "Step 11: Observations Endpoint")
    
except Exception as e:
    print(f"\n[ERROR] {e}")
    import traceback
    traceback.print_exc()
finally:
    ssh.close()

print("\n" + "="*70)
print("  [COMPLETE] MINDEX Fix Done!")
print("="*70)
print("\nVerify from Windows:")
print("  Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats")
print("\nTest website:")
print("  http://localhost:3010/natureos/mindex")
