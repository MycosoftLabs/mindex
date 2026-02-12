#!/usr/bin/env python3
"""Create final missing tables and test"""
import paramiko
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = "Mushroom1!Mushroom1!"
PG_USER = "mycosoft"
PG_DB = "mindex"

def run(ssh, cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120, get_pty=True)
    stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    import re
    out = re.sub(r'\x1b\[[0-9;]*m', '', out)
    print(out + "\n")
    return out

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VM_HOST, username=VM_USER, password=VM_PASS, timeout=30,
           look_for_keys=False, allow_agent=False)

try:
    print("[1] Create core.taxon_external_id")
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c \"CREATE TABLE IF NOT EXISTS core.taxon_external_id (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), taxon_id integer NOT NULL, source text NOT NULL, external_id text NOT NULL, metadata jsonb NOT NULL DEFAULT '{{}}', created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source, external_id));\"")
    
    print("[2] Create core.taxon_synonym")
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c \"CREATE TABLE IF NOT EXISTS core.taxon_synonym (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), taxon_id integer NOT NULL, synonym text NOT NULL, source text, created_at timestamptz NOT NULL DEFAULT now());\"")
    
    print("[3] Restart API")
    run(ssh, "docker restart mindex-api")
    time.sleep(10)
    
    print("[4] Test Stats")
    out = run(ssh, "curl -s http://localhost:8000/api/mindex/stats")
    
    if "{" in out and "total_taxa" in out:
        print("\n[SUCCESS] Stats working!")
    else:
        print("\n[ERROR] Stats still failing. API logs:")
        run(ssh, "docker logs mindex-api --tail 50")
    
    print("[5] Test Observations")
    out = run(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3"')
    
except Exception as e:
    print(f"\n[ERROR] {e}")
finally:
    ssh.close()

print("\n[DONE]")
