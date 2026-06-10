"""Check which migrations/tables exist on VM 189."""
from __future__ import annotations

from pathlib import Path

import paramiko

CREDS = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local")

SQL = """
SELECT schemaname, tablename FROM pg_tables
WHERE schemaname IN ('media','bio','core','obs')
ORDER BY schemaname, tablename;

SELECT count(*) AS media_image_count FROM media.image;
SELECT count(*) AS genetic_sequence_count FROM bio.genetic_sequence;
"""


def main():
    pw = ""
    for line in CREDS.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() in ("VM_PASSWORD", "VM_SSH_PASSWORD"):
                pw = v.strip()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.0.189", username="mycosoft", password=pw, timeout=20)
    cmd = (
        "sudo docker exec -i mindex-postgres psql -U mycosoft -d mindex -v ON_ERROR_STOP=0 <<'EOSQL'\n"
        + SQL
        + "\nEOSQL"
    )
    i, o, e = c.exec_command(cmd, timeout=60)
    print((o.read() or b"").decode())
    print((e.read() or b"").decode())
    c.close()


if __name__ == "__main__":
    main()
