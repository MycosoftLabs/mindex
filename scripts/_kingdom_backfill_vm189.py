# AWS topology correction 2026-11-27: production is AWS-only; this file's legacy on-prem references are not deployment authority. See AWS_INFRA_REFERENCE.md for its replacement and disposition. Loopback development and physical-device fixtures are separate.

if __name__ == "__main__":
    raise SystemExit('Retired on-prem utility: use the AWS release/SSM procedure in AWS_INFRA_REFERENCE.md; no local production servers exist.')

import paramiko
from pathlib import Path

pw = [l.split("=", 1)[1].strip() for l in Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local").read_text().splitlines() if l.startswith("VM_PASSWORD=")][0]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.189", username="mycosoft", password=pw, timeout=30)
cmd = "sudo docker exec -d mindex-etl python -m mindex_etl.jobs.backfill_kingdom_lineage"
i, o, e = c.exec_command(cmd, timeout=30)
print((o.read() + e.read()).decode())
c.close()
print("kingdom backfill started in background")
