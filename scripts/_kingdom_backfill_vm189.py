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
