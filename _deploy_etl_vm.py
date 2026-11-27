#!/usr/bin/env python3
# AWS topology correction 2026-11-27: production is AWS-only; this file's legacy on-prem references are not deployment authority. See AWS_INFRA_REFERENCE.md for its replacement and disposition. Loopback development and physical-device fixtures are separate.
"""Deploy ETL code to VM 189 and restart the aggressive runner."""

if __name__ == "__main__":
    raise SystemExit('Retired on-prem utility: use the AWS release/SSM procedure in AWS_INFRA_REFERENCE.md; no local production servers exist.')


import paramiko
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

def main():
    VM_PASS = os.environ.get("VM_PASSWORD")
    if not VM_PASS:
        print("ERROR: VM_PASSWORD not set")
        return 1
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # Connect to VM 189 directly
    print("Connecting to VM 189...")
    try:
        ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)
        print("Connected to VM 189!")
        
        # Pull the latest code
        print("Pulling latest code...")
        stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && git pull origin main", timeout=120)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        print(f"Pull output: {out}")
        if err:
            print(f"Pull errors: {err}")
        print(f"Pull exit code: {exit_code}")
        
        # Kill any existing ETL process
        print("\nKilling any existing ETL process...")
        stdin, stdout, stderr = ssh.exec_command("pkill -f aggressive_runner || true", timeout=30)
        stdout.channel.recv_exit_status()
        
        # Restart the ETL runner
        print("\nRestarting aggressive ETL runner...")
        stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && nohup ./start_etl.sh > etl.log 2>&1 &", timeout=30)
        stdout.channel.recv_exit_status()
        
        time.sleep(3)
        
        # Check if it's running
        print("\nChecking if ETL is running...")
        stdin, stdout, stderr = ssh.exec_command("ps aux | grep aggressive_runner | grep -v grep", timeout=30)
        out = stdout.read().decode("utf-8", errors="ignore")
        if "aggressive_runner" in out:
            print("ETL runner is running!")
            print(out)
        else:
            print("Warning: ETL runner may not have started. Checking logs...")
            stdin, stdout, stderr = ssh.exec_command("tail -50 /home/mycosoft/mindex/etl.log", timeout=30)
            print(stdout.read().decode("utf-8", errors="ignore"))
        
        ssh.close()
        print("\nDeployment complete!")
        return 0
        
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
