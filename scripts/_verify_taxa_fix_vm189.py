import os
import paramiko
from pathlib import Path

creds = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local")
local_run_all = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MINDEX\mindex\mindex_etl\jobs\run_all.py")
pw = ""
for line in creds.read_text().splitlines():
    if line.startswith("VM_PASSWORD="):
        pw = line.split("=", 1)[1].strip()

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.189", username="mycosoft", password=pw, timeout=30)
sftp = c.open_sftp()
sftp.put(str(local_run_all), "/home/mycosoft/mindex/mindex_etl/jobs/run_all.py")
sftp.close()

cmds = [
    "sudo docker restart mindex-etl",
    "sleep 10",
    "sudo docker exec mindex-etl python -c 'from mindex_etl.jobs.run_all import create_job_registry; print(\"jobs\", len(create_job_registry()))'",
    "sudo docker exec mindex-etl python -m mindex_etl.jobs.sync_gbif_occurrences --max-pages 1 --domain-mode fungi 2>&1 | tail -20",
    "sudo docker exec mindex-etl python -m mindex_etl.scheduler --once --max-pages 2 2>&1 | tail -35",
    "sudo docker exec mindex-postgres psql -U mindex -d mindex -c \"SELECT source, count(*) FROM core.taxon GROUP BY source ORDER BY count DESC LIMIT 10;\"",
    "sudo docker logs mindex-etl --tail 20 2>&1 | grep -iE 'mycobank|gbif|inat_taxa|failed|Completed' || true",
]
for cmd in cmds:
    i, o, e = c.exec_command(cmd, timeout=300)
    print(">", cmd)
    print((o.read() or e.read()).decode()[:3000])
c.close()
