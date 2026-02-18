#!/usr/bin/env python3
"""Check MINDEX database counts - Feb 12, 2026"""

import os
import paramiko
import sys

sys.stdout.reconfigure(encoding='utf-8')

VM_PASS = os.environ.get("VM_PASSWORD")
if not VM_PASS:
    print("ERROR: VM_PASSWORD not set")
    exit(1)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)

print("Checking database counts...")

# Taxon count
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as taxon_count FROM core.taxon"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print("Taxon count:")
print(stdout.read().decode())

# Taxa by source (top 12)
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT source, COUNT(1) as n FROM core.taxon GROUP BY source ORDER BY n DESC LIMIT 12"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
print("Taxa by source (top 12):")
print(stdout.read().decode(errors="replace"))

# Observation count
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as observation_count FROM obs.observation"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print("Observation count:")
print(stdout.read().decode())

# Compound count
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as compound_count FROM bio.compound"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print("Compound count:")
print(stdout.read().decode())

# Sequence count
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as sequence_count FROM bio.genetic_sequence"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print("Genetic sequence count:")
print(stdout.read().decode())

# Publications count
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as publications_count FROM core.publications"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print("Publications count:")
print(stdout.read().decode())

# Schemas (helps debug when migrations aren't applied)
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "\\d bio.genetic_sequence"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
print("Schema: bio.genetic_sequence")
print(stdout.read().decode(errors="replace"))

cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "\\d bio.compound"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
print("Schema: bio.compound")
print(stdout.read().decode(errors="replace"))

cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "\\d core.publications"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
print("Schema: core.publications")
print(stdout.read().decode(errors="replace"))
print(stderr.read().decode(errors="replace"))

# Recent taxa with author column
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT scientific_name, author, rank, source FROM core.taxon ORDER BY created_at DESC LIMIT 10"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print("Recent taxa (with author column):")
print(stdout.read().decode())

ssh.close()
print("Done!")
