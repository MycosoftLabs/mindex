#!/usr/bin/env python3
# AWS topology correction 2026-11-27: production is AWS-only; this file's legacy on-prem references are not deployment authority. See AWS_INFRA_REFERENCE.md for its replacement and disposition. Loopback development and physical-device fixtures are separate.
"""Test MINDEX VM connection - Feb 11, 2026"""

if __name__ == "__main__":
    raise SystemExit('Retired on-prem utility: use the AWS release/SSM procedure in AWS_INFRA_REFERENCE.md; no local production servers exist.')


import os
import paramiko
import sys

sys.stdout.reconfigure(encoding='utf-8')

VM_PASS = os.environ.get("VM_PASSWORD")
print(f"Password set: {'Yes' if VM_PASS else 'No'}")
print(f"Password length: {len(VM_PASS) if VM_PASS else 0}")

try:
    print("\nAttempting to connect to 192.168.0.189...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30, look_for_keys=False, allow_agent=False)
    print("✓ Connected successfully!")
    
    print("\nTesting simple command...")
    stdin, stdout, stderr = ssh.exec_command("whoami", timeout=10)
    print(f"User: {stdout.read().decode().strip()}")
    
    ssh.close()
except Exception as e:
    print(f"✗ Connection failed: {e}")
    sys.exit(1)
