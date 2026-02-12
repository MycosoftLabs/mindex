#!/usr/bin/env python3
"""Test MINDEX API endpoints"""
import paramiko
import os

VM_IP = "192.168.0.189"
VM_USER = "mycosoft"
VM_PASS = os.environ.get("VM_PASSWORD", "Mushroom1!Mushroom1!")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VM_IP, username=VM_USER, password=VM_PASS, timeout=30)

print("=== Testing MINDEX API from VM ===")

endpoints = [
    "/",
    "/health",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/api/v1/health",
]

for ep in endpoints:
    stdin, stdout, stderr = ssh.exec_command(f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:8000{ep}", timeout=10)
    code = stdout.read().decode().strip()
    print(f"  {ep}: {code}")

ssh.close()
