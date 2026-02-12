#!/usr/bin/env python3
"""Fix obs.observation table structure and update stats router"""
import paramiko
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = "Mushroom1!Mushroom1!"
PG_USER = "mycosoft"
PG_DB = "mindex"
MINDEX_DIR = "/home/mycosoft/mindex"

def run(ssh, cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=180, get_pty=True)
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
    print("="*70)
    print("[1] Check current obs.observation structure")
    print("="*70)
    run(ssh, f"docker exec mindex-postgres psql -U {PG_USER} -d {PG_DB} -c '\\d obs.observation'")
    
    print("="*70)
    print("[2] Fix stats.py to use latitude/longitude instead of location")
    print("="*70)
    
    # Update stats.py file on VM
    stats_fix = """cd /home/mycosoft/mindex && cat > /tmp/fix_stats.py << 'EEOF'
# Fix stats query to use lat/lng instead of location
import re

with open('mindex_api/routers/stats.py', 'r') as f:
    content = f.read()

# Replace location IS NOT NULL with latitude IS NOT NULL AND longitude IS NOT NULL
content = content.replace(
    'FROM obs.observation WHERE location IS NOT NULL',
    'FROM obs.observation WHERE latitude IS NOT NULL AND longitude IS NOT NULL'
)

# Replace media check
content = content.replace(
    "WHERE media IS NOT NULL AND media::text != '[]'",
    "WHERE media IS NOT NULL AND jsonb_array_length(media) > 0"
)

with open('mindex_api/routers/stats.py', 'w') as f:
    f.write(content)

print("Stats router fixed!")
EEOF
python3 /tmp/fix_stats.py"""
    
    out = run(ssh, stats_fix)
    
    print("="*70)
    print("[3] Fix observations.py to use latitude/longitude")
    print("="*70)
    
    obs_fix = """cd /home/mycosoft/mindex && cat > /tmp/fix_obs.py << 'EEOF'
# Simplify observations query
with open('mindex_api/routers/observations.py', 'r') as f:
    lines = f.readlines()

# Find and comment out location_geojson line
new_lines = []
for line in lines:
    if 'ST_AsGeoJSON' in line or 'location_geojson' in line:
        new_lines.append('            -- ' + line)
    elif 'loc = data.pop("location_geojson"' in line:
        new_lines.append('        # ' + line)
    elif 'data["location"] = json.loads(loc)' in line:
        new_lines.append('        # ' + line)
    else:
        new_lines.append(line)

with open('mindex_api/routers/observations.py', 'w') as f:
    f.writelines(new_lines)

print("Observations router fixed!")
EEOF
python3 /tmp/fix_obs.py"""
    
    out = run(ssh, obs_fix)
    
    print("="*70)
    print("[4] Restart API with fixed code")
    print("="*70)
    run(ssh, "docker restart mindex-api")
    print("\n[WAIT] 15 seconds...")
    time.sleep(15)
    
    print("="*70)
    print("[5] Test Health")
    print("="*70)
    run(ssh, "curl -s http://localhost:8000/api/mindex/health")
    
    print("="*70)
    print("[6] Test Stats (Should Work Now!)")
    print("="*70)
    out = run(ssh, "curl -s http://localhost:8000/api/mindex/stats")
    
    if "{" in out and "total_taxa" in out:
        print("[SUCCESS] Stats endpoint WORKING!")
    else:
        print(f"[ERROR] Stats failed: {out[:500]}")
        run(ssh, "docker logs mindex-api --tail 20")
    
    print("="*70)
    print("[7] Test Observations")
    print("="*70)
    out = run(ssh, 'curl -s "http://localhost:8000/api/mindex/observations?limit=3"')
    
    if "{" in out and ("data" in out or "observations" in out):
        print("[SUCCESS] Observations endpoint WORKING!")
    
except Exception as e:
    print(f"\n[ERROR] {e}")
finally:
    ssh.close()

print("\n[DONE] MINDEX API Fixed!")
print("\nTest from Windows:")
print("  Invoke-RestMethod http://192.168.0.189:8000/api/mindex/stats")
print("  http://localhost:3010/natureos/mindex")
