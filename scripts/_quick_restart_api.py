#!/usr/bin/env python3
# AWS topology correction 2026-11-27: production is AWS-only; this file's legacy on-prem references are not deployment authority. See AWS_INFRA_REFERENCE.md for its replacement and disposition. Loopback development and physical-device fixtures are separate.
"""Quick restart of MINDEX API after code changes."""

if __name__ == "__main__":
    raise SystemExit('Retired on-prem utility: use the AWS release/SSM procedure in AWS_INFRA_REFERENCE.md; no local production servers exist.')

import os
import paramiko
import sys
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASSWORD = os.environ.get("VM_PASSWORD", "")

def main():
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(VM_HOST, username=VM_USER, password=VM_PASSWORD, timeout=15)
        
        # Pull code
        print("[*] Pulling code...")
        stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && git pull", timeout=30)
        stdout.channel.recv_exit_status()
        out = stdout.read().decode()
        print(out[:300])
        
        # Restart API via docker command
        print("[*] Restarting API container...")
        stdin, stdout, stderr = ssh.exec_command(
            "docker restart mindex-api",
            timeout=30
        )
        exit_code = stdout.channel.recv_exit_status()
        
        if exit_code == 0:
            print("[+] Container restarted")
        else:
            print(f"[!] Restart exit code: {exit_code}")
            print(stderr.read().decode()[:300])
        
        # Wait
        time.sleep(10)
        
        # Check health
        print("[*] Checking health...")
        stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/health", timeout=15)
        health = stdout.read().decode()
        print(f"Health: {health[:200]}")
        
        ssh.close()
        return 0
        
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
