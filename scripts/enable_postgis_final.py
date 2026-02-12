#!/usr/bin/env python3
"""Enable PostGIS extension in MINDEX database"""
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

print("\n[*] Connecting...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
           look_for_keys=False, allow_agent=False)
print("[OK]\n")

try:
    print("="*70)
    print("Step 1: Check PostGIS Package")
    print("="*70)
    run(ssh, "docker exec mindex-postgres dpkg -l | grep postgis || echo 'PostGIS not installed'")
    
    print("="*70)
    print("Step 2: List Current Extensions")
    print("="*70)
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dx'")
    
    print("="*70)
    print("Step 3: Enable PostGIS Extension")
    print("="*70)
    # Try as mycosoft user first
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c 'CREATE EXTENSION IF NOT EXISTS postgis;'")
    
    # Also try as superuser in postgres database
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d postgres -c 'CREATE EXTENSION IF NOT EXISTS postgis;' 2>&1 || echo 'Tried postgres db'")
    
    # Try in mindex database again
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c 'CREATE EXTENSION IF NOT EXISTS postgis CASCADE;' 2>&1")
    
    print("="*70)
    print("Step 4: Verify PostGIS Enabled")
    print("="*70)
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c 'SELECT PostGIS_version();' 2>&1")
    
    print("="*70)
    print("Step 5: Create obs Schema and Table")
    print("="*70)
    sql = """
    CREATE SCHEMA IF NOT EXISTS obs;
    CREATE TABLE IF NOT EXISTS obs.observation (
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
    CREATE INDEX IF NOT EXISTS idx_observation_location ON obs.observation USING GIST (location);
    """
    
    run(ssh, f"echo \"{sql}\" | docker exec -i mindex-postgres psql -U {PG_USER} -d {PG_DB}")
    
    print("="*70)
    print("Step 6: Verify Table Created")
    print("="*70)
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dt obs.*'")
    
    print("="*70)
    print("Step 7: Create bio Schema Tables")
    print("="*70)
    bio_sql = """
    CREATE SCHEMA IF NOT EXISTS bio;
    CREATE TABLE IF NOT EXISTS bio.taxon_trait (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        taxon_id uuid NOT NULL REFERENCES core.taxon (id) ON DELETE CASCADE,
        trait_name text NOT NULL,
        value_text text,
        value_numeric double precision,
        value_unit text,
        source text,
        metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS bio.genome (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        taxon_id uuid NOT NULL REFERENCES core.taxon (id) ON DELETE CASCADE,
        source text NOT NULL,
        accession text NOT NULL,
        assembly_level text,
        release_date date,
        metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """
    run(ssh, f"echo \"{bio_sql}\" | docker exec -i mindex-postgres psql -U {PG_USER} -d {PG_DB}")
    
    print("="*70)
    print("Step 8: Restart API")
    print("="*70)
    run(ssh, "docker restart mindex-api")
    print("\n[WAIT] 10 seconds...")
    time.sleep(10)
    
    print("="*70)
    print("Step 9: Test Stats Endpoint")
    print("="*70)
    run(ssh, "curl -s http://localhost:8000/api/mindex/stats | python3 -m json.tool")
    
    print("="*70)
    print("Step 10: Test Observations Endpoint")
    print("="*70)
    run(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3" | python3 -m json.tool')
    
except Exception as e:
    print(f"\n[ERROR] {e}")
    import traceback
    traceback.print_exc()
finally:
    ssh.close()

print("\n" + "="*70)
print("  [SUCCESS] All schemas created!")
print("="*70)
