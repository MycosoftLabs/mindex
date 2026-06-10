#!/usr/bin/env python3
import os, sys
from pathlib import Path
import paramiko

creds = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local")
pw = next((l.split("=",1)[1].strip() for l in creds.read_text().splitlines() if l.startswith("VM_PASSWORD=")), "")
ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.0.189", username="mycosoft", password=pw, timeout=30)
_, o, _ = ssh.exec_command(
    "sudo docker exec mindex-postgres psql -U mycosoft -d mindex -c "
    "\"SELECT column_name FROM information_schema.columns WHERE table_schema='media' AND table_name='image' ORDER BY 1;\"",
    timeout=60,
)
print(o.read().decode(errors="replace"))
ssh.close()
