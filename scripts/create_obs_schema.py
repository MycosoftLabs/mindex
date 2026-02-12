#!/usr/bin/env python3
"""Create obs schema and tables"""
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
    
    if out:
        print(out)
    return out

print("\n[*] Connecting...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
           look_for_keys=False, allow_agent=False)
print("[OK] Connected!\n")

try:
    # Create obs schema
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
    
    # Write SQL to temp file
    run_cmd(ssh, f"cat > /tmp/create_obs.sql << 'EEOF'\n{sql}\nEEOF", 
            "Step 1: Create SQL Script")
    
    # Run SQL
    run_cmd(ssh, f"docker exec -i mindex-postgres psql -U {PG_USER} -d {PG_DB} < /tmp/create_obs.sql", 
            "Step 2: Create obs Schema and Table")
    
    # Verify
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\dt obs.*'", 
            "Step 3: Verify obs.observation Table")
    
    # Check counts again
    run_cmd(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -t -c 'SELECT COUNT(*) FROM obs.observation;'", 
            "Step 4: Count Observations")
    
    # Restart API
    run_cmd(ssh, "docker restart mindex-api", 
            "Step 5: Restart API")
    
    print("\n[WAIT] 10 seconds...")
    time.sleep(10)
    
    # Test endpoints
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/health", 
            "Step 6: Health")
    
    run_cmd(ssh, "curl -s http://localhost:8000/api/mindex/stats | head -100", 
            "Step 7: Stats")
    
    run_cmd(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3"', 
            "Step 8: Observations")
    
except Exception as e:
    print(f"\n[ERROR] {e}")
    import traceback
    traceback.print_exc()
finally:
    ssh.close()

print("\n[DONE] Complete!")
