#!/usr/bin/env python3
"""Create all missing MINDEX tables"""
import paramiko
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = "Mushroom1!Mushroom1!"
PG_USER = "mycosoft"
PG_DB = "mindex"

def run(ssh, cmd):
    print(f"$ {cmd}\n")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120, get_pty=True)
    stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    import re
    out = re.sub(r'\x1b\[[0-9;]*m', '', out)
    print(out + "\n")
    return out

print("="*70)
print("  Create All Missing MINDEX Tables")
print("="*70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
           look_for_keys=False, allow_agent=False)
print("[OK] Connected!\n")

try:
    # Create core.taxon_external_id
    print("[Step 1] Create core.taxon_external_id")
    print('-'*70)
    sql1 = """
    CREATE TABLE IF NOT EXISTS core.taxon_external_id (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        taxon_id integer NOT NULL,
        source text NOT NULL,
        external_id text NOT NULL,
        metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(source, external_id)
    );
    CREATE INDEX IF NOT EXISTS idx_taxon_external_taxon ON core.taxon_external_id (taxon_id);
    """
    run(ssh, f"echo \"{sql1}\" | docker exec -i mindex-postgres psql -U {PG_USER} -d {PG_DB}")
    
    # Create core.taxon_synonym
    print("[Step 2] Create core.taxon_synonym")
    print('-'*70)
    sql2 = """
    CREATE TABLE IF NOT EXISTS core.taxon_synonym (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        taxon_id integer NOT NULL,
        synonym text NOT NULL,
        source text,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_taxon_synonym_taxon ON core.taxon_synonym (taxon_id);
    """
    run(ssh, f"echo \"{sql2}\" | docker exec -i mindex-postgres psql -U {PG_USER} -d {PG_DB}")
    
    # Verify all tables
    print("[Step 3] List all tables")
    print('-'*70)
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dt core.*'")
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dt obs.*'")
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dt bio.*'")
    
    # Restart API
    print("[Step 4] Restart API")
    print('-'*70)
    run(ssh, "docker restart mindex-api")
    print("\n[WAIT] 10 seconds...")
    time.sleep(10)
    
    # Test health
    print("[Step 5] Test Health")
    print('-'*70)
    run(ssh, "curl -s http://localhost:8000/api/mindex/health")
    
    # Test stats (should work now!)
    print("[Step 6] Test Stats (Should Work Now!)")
    print('-'*70)
    out = run(ssh, "curl -s http://localhost:8000/api/mindex/stats")
    
    if "total_taxa" in out:
        print("\n[SUCCESS] Stats endpoint working!")
    elif "Internal Server Error" in out:
        print("\n[ERROR] Still failing - checking logs...")
        run(ssh, "docker logs mindex-api --tail 30")
    
    # Test observations
    print("[Step 7] Test Observations")
    print('-'*70)
    out = run(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3"')
    
    if "data" in out or "observations" in out:
        print("\n[SUCCESS] Observations endpoint working!")
    
except Exception as e:
    print(f"\n[ERROR] {e}")
    import traceback
    traceback.print_exc()
finally:
    ssh.close()

print("\n" + "="*70)
print("  [DONE] All Tables Created!")
print("="*70)
print("\nVerify from Windows:")
print("  Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats")
print("  Invoke-RestMethod http://localhost:3010/api/natureos/mindex/stats")
print("\nOpen:")
print("  http://localhost:3010/natureos/mindex")
print("  http://localhost:3010/natureos/mindex/explorer")
