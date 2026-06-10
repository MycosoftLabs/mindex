import paramiko
from pathlib import Path

pw = [l.split("=", 1)[1].strip() for l in Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local").read_text().splitlines() if l.startswith("VM_PASSWORD=")][0]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.189", username="mycosoft", password=pw, timeout=30)
queries = [
    ("taxa by source", "SELECT source, count(*) FROM core.taxon GROUP BY source ORDER BY count DESC;"),
    ("fungi kingdom", "SELECT count(*) FROM core.taxon WHERE kingdom='Fungi';"),
    ("pleurotus search", "SELECT canonical_name, source FROM core.taxon WHERE canonical_name ILIKE '%Pleurotus%' LIMIT 5;"),
]
for label, sql in queries:
    cmd = f"sudo docker exec mindex-postgres psql -U mindex -d mindex -c \"{sql}\""
    i, o, e = c.exec_command(cmd, timeout=30)
    print(f"=== {label} ===")
    print(o.read().decode())
c.close()
