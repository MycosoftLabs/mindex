#!/usr/bin/env python3
"""Get recent MINDEX API logs."""
import paramiko
import sys

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASSWORD = "Mushroom1!Mushroom1!"

def main():
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(VM_HOST, username=VM_USER, password=VM_PASSWORD, timeout=15)
        
        print("[*] Getting recent API logs...")
        stdin, stdout, stderr = ssh.exec_command("docker logs mindex-api --tail 50 2>&1", timeout=30)
        logs = stdout.read().decode()
        print(logs)
        
        ssh.close()
        return 0
        
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
