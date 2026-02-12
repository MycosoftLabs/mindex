#!/usr/bin/env python3
"""Fix MINDEX schema without PostGIS (use lat/lng columns instead)"""
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
print("  MINDEX Schema Fix (Without PostGIS)")
print("="*70)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
           look_for_keys=False, allow_agent=False)
print("[OK] Connected!\n")

try:
    print("[Step 1] Check current core.taxon structure")
    print('-'*70)
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\d core.taxon'")
    
    print("[Step 2] Create obs.observation WITHOUT PostGIS (using lat/lng)")
    print('-'*70)
    sql = """
    CREATE SCHEMA IF NOT EXISTS obs;
    
    DROP TABLE IF EXISTS obs.observation CASCADE;
    
    CREATE TABLE obs.observation (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        taxon_id integer,
        source text NOT NULL,
        source_id text,
        observer text,
        observed_at timestamptz NOT NULL,
        latitude double precision,
        longitude double precision,
        accuracy_m double precision,
        media jsonb NOT NULL DEFAULT '[]'::jsonb,
        notes text,
        metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    
    CREATE INDEX IF NOT EXISTS idx_observation_taxon ON obs.observation (taxon_id);
    CREATE INDEX IF NOT EXISTS idx_observation_source ON obs.observation (source);
    CREATE INDEX IF NOT EXISTS idx_observation_observed_at ON obs.observation (observed_at);
    CREATE INDEX IF NOT EXISTS idx_observation_lat_lng ON obs.observation (latitude, longitude);
    """
    
    run(ssh, f"echo \"{sql}\" | docker exec -i mindex-postgres psql -U {PG_USER} -d {PG_DB}")
    
    print("[Step 3] Verify table created")
    print('-'*70)
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dt obs.*'")
    
    print("[Step 4] Check if bio.taxon_trait needs fixing")
    print('-'*70)
    bio_sql = """
    CREATE SCHEMA IF NOT EXISTS bio;
    
    DROP TABLE IF EXISTS bio.taxon_trait CASCADE;
    DROP TABLE IF NOT EXISTS bio.genome CASCADE;
    
    CREATE TABLE bio.taxon_trait (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        taxon_id integer NOT NULL,
        trait_name text NOT NULL,
        value_text text,
        value_numeric double precision,
        value_unit text,
        source text,
        metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    
    CREATE TABLE bio.genome (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        taxon_id integer NOT NULL,
        source text NOT NULL,
        accession text NOT NULL,
        assembly_level text,
        release_date date,
        metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """
    run(ssh, f"echo \"{bio_sql}\" | docker exec -i mindex-postgres psql -U {PG_USER} -d {PG_DB}")
    
    print("[Step 5] Restart API")
    print('-'*70)
    run(ssh, "docker restart mindex-api")
    print("\n[WAIT] 10 seconds...")
    time.sleep(10)
    
    print("[Step 6] Test Health")
    print('-'*70)
    run(ssh, "curl -s http://localhost:8000/api/mindex/health")
    
    print("[Step 7] Test Stats")
    print('-'*70)
    out = run(ssh, "curl -s http://localhost:8000/api/mindex/stats 2>&1")
    
    if "Internal Server Error" not in out:
        print("\n[SUCCESS] Stats endpoint working!")
    else:
        print("\n[INFO] Checking API logs for error...")
        run(ssh, "docker logs mindex-api --tail 20")
    
    print("[Step 8] Test Observations")
    print('-'*70)
    run(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3"')
    
except Exception as e:
    print(f"\n[ERROR] {e}")
    import traceback
    traceback.print_exc()
finally:
    ssh.close()

print("\n" + "="*70)
print("  [DONE] Schema Fixed (Without PostGIS)")
print("="*70)
print("\nTest:")
print("  Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats")
