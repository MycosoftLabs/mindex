#!/usr/bin/env python3
"""Check MINDEX VM status"""
import paramiko
import os

VM_IP = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = os.environ.get("VM_PASSWORD", "Mushroom1!Mushroom1!")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VM_IP, username=VM_USER, password=VM_PASS, timeout=30)

print("=== Docker Containers ===")
stdin, stdout, stderr = ssh.exec_command("docker ps -a", timeout=30)
print(stdout.read().decode('utf-8', errors='ignore'))

print("\n=== MINDEX API Logs (last 30) ===")
stdin, stdout, stderr = ssh.exec_command("docker logs mindex-mindex-api-1 --tail 30 2>&1 || docker logs mindex_mindex-api_1 --tail 30 2>&1 || echo 'Could not get logs'", timeout=30)
print(stdout.read().decode('utf-8', errors='ignore'))

ssh.close()
