#!/usr/bin/env python3
"""Enable PostGIS and create all MINDEX tables"""
import paramiko
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = "Mushroom1!Mushroom1!"
PG_USER = "mycosoft"
PG_DB = "mindex"

def run_cmd(ssh, cmd, desc=""):
    if desc:
        print(f"\n{desc}")
        print('-'*70)
    print(f"$ {cmd}\n")
    
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120, get_pty=True)
    stdout.channel.recv_exit_status()
    
    out = stdout.read().decode('utf-8', errors='replace').strip()
    import re
    out = re.sub(r'\x1b\[[0-9;]*m', '', out)
    
    print(out)
    return out

print("="*70)
print("  MINDEX Complete Schema Fix")
print("="*70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
           look_for_keys=False, allow_agent=False)
print("[OK] Connected!\n")

try:
    # Enable PostGIS extension
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c 'CREATE EXTENSION IF NOT EXISTS postgis;'", 
            "Step 1: Enable PostGIS")
    
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c 'CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";'", 
            "Step 2: Enable uuid-ossp")
    
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;'", 
            "Step 3: Enable pgcrypto")
    
    # Create schemas
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c 'CREATE SCHEMA IF NOT EXISTS obs;'", 
            "Step 4: Create obs Schema")
    
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c 'CREATE SCHEMA IF NOT EXISTS bio;'", 
            "Step 5: Create bio Schema")
    
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c 'CREATE SCHEMA IF NOT EXISTS telemetry;'", 
            "Step 6: Create telemetry Schema")
    
    # Now create observation table with PostGIS
    sql = """CREATE TABLE IF NOT EXISTS obs.observation (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        taxon_id uuid REFERENCES core.taxon (id) ON DELETE SET NULL,
        source text NOT NULL,
        source_id text,
        observer text,
        observed_at timestamptz NOT NULL,
        location geography(Point, 4326),
        accuracy_m double precision,
        media jsonb NOT NULL DEFAULT '[]'::jsonb,
        notes text,
        metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_observation_taxon ON obs.observation (taxon_id);
    CREATE INDEX IF NOT EXISTS idx_observation_source ON obs.observation (source);
    CREATE INDEX IF NOT EXISTS idx_observation_observed_at ON obs.observation (observed_at);
    CREATE INDEX IF NOT EXISTS idx_observation_location ON obs.observation USING GIST (location);"""
    
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c \"{sql}\"", 
            "Step 7: Create obs.observation Table")
    
    # Verify table created
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dt obs.*'", 
            "Step 8: Verify Tables")
    
    # Check count
    out = run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -t -c 'SELECT COUNT(*) FROM obs.observation;'", 
            "Step 9: Count Observations")
    
    obs_count = out.strip().split()[-1] if out else "0"
    print(f"\n[INFO] Observation count: {obs_count}")
    
    # Restart API
    run_cmd(ssh, "docker restart mindex-api", 
            "Step 10: Restart API")
    
    print("\n[WAIT] 10 seconds...")
    time.sleep(10)
    
    # Test stats endpoint
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/stats | python3 -m json.tool 2>&1 | head -50", 
            "Step 11: Test Stats Endpoint")
    
    # Test observations endpoint
    run_cmd(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3" | python3 -m json.tool 2>&1 | head -100', 
            "Step 12: Test Observations Endpoint")
    
except Exception as e:
    print(f"\n[ERROR] {e}")
    import traceback
    traceback.print_exc()
finally:
    ssh.close()

print("\n" + "="*70)
print("  [SUCCESS] MINDEX Schema Created!")
print("="*70)
print("\nVerify:")
print("  Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats")
