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

# Observation count
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT COUNT(*) as observation_count FROM obs.observation"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print("Observation count:")
print(stdout.read().decode())

# Recent taxa with author column
cmd = 'docker exec mindex-postgres psql -U mycosoft -d mindex -c "SELECT scientific_name, author, rank, source FROM core.taxon ORDER BY created_at DESC LIMIT 10"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print("Recent taxa (with author column):")
print(stdout.read().decode())

ssh.close()
print("Done!")
