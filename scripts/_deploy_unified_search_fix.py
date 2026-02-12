#!/usr/bin/env python3
"""Deploy unified_search.py fix to MINDEX VM."""
import paramiko
import sys
import time

VM_HOST = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASSWORD = "Mushroom1!Mushroom1!"

def main():
    try:
        print("[*] Connecting to MINDEX VM...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(VM_HOST, username=VM_USER, password=VM_PASSWORD, timeout=15)
        
        # Pull latest code
        print("[*] Pulling latest code...")
        stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && git pull", timeout=30)
        stdout.channel.recv_exit_status()
        print(stdout.read().decode()[:500])
        
        # Find service name
        print("[*] Finding MINDEX API service name...")
        stdin, stdout, stderr = ssh.exec_command(
            "cd /home/mycosoft/mindex && docker-compose ps --services | grep -i api",
            timeout=15
        )
        service_name = stdout.read().decode().strip() or "api"
        print(f"[*] Service name: {service_name}")
        
        # Restart mindex-api container
        print(f"[*] Restarting {service_name} container...")
        stdin, stdout, stderr = ssh.exec_command(
            f"cd /home/mycosoft/mindex && docker-compose stop {service_name} && docker-compose build --no-cache {service_name} && docker-compose up -d {service_name}",
            timeout=120
        )
        exit_code = stdout.channel.recv_exit_status()
        
        if exit_code != 0:
            print("[!] Restart failed")
            print(stderr.read().decode()[:500])
            return 1
        
        print("[+] Container restarted")
        
        # Wait for startup
        print("[*] Waiting for API to be ready...")
        time.sleep(8)
        
        # Test health
        stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8000/health", timeout=15)
        health = stdout.read().decode()
        print(f"[*] Health check: {health[:200]}")
        
        ssh.close()
        print("[+] Deployment complete")
        return 0
        
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
