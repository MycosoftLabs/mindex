#!/usr/bin/env python3
"""Check MINDEX container status."""
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
        
        # Check running containers
        print("[*] Checking running containers...")
        stdin, stdout, stderr = ssh.exec_command("docker ps --format '{{.Names}} - {{.Status}}' | grep mindex", timeout=15)
        containers = stdout.read().decode()
        print(containers if containers else "[!] No mindex containers running")
        
        # Check all mindex containers
        print("\n[*] All mindex containers (including stopped)...")
        stdin, stdout, stderr = ssh.exec_command("docker ps -a --format '{{.Names}} - {{.Status}}' | grep mindex", timeout=15)
        all_containers = stdout.read().decode()
        print(all_containers)
        
        # Check docker-compose services
        print("\n[*] Docker-compose services...")
        stdin, stdout, stderr = ssh.exec_command("cd /home/mycosoft/mindex && docker-compose ps", timeout=15)
        services = stdout.read().decode()
        print(services)
        
        # Check logs for API container
        print("\n[*] Recent API logs...")
        stdin, stdout, stderr = ssh.exec_command("docker logs mindex-api --tail 30 2>&1 || docker logs mindex_api_1 --tail 30 2>&1", timeout=15)
        logs = stdout.read().decode()
        print(logs[:1000] if logs else "[!] No logs found")
        
        ssh.close()
        return 0
        
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
