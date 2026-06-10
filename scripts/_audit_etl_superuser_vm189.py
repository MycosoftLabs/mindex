"""MINDEX audit with postgres superuser + ETL DB user check."""
from __future__ import annotations

import os
from pathlib import Path

import paramiko

CREDS = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\MAS\mycosoft-mas\.credentials.local")

SQL = r"""
SELECT 'COMPOUND_COUNT' as k, count(*)::text as v FROM bio.compound
UNION ALL SELECT 'COMPOUND_PUBCHEM', count(*)::text FROM bio.compound WHERE pubchem_id IS NOT NULL
UNION ALL SELECT 'COMPOUND_CHEMSPIDER', count(*)::text FROM bio.compound WHERE chemspider_id IS NOT NULL
UNION ALL SELECT 'GENOME_COUNT', count(*)::text FROM bio.genome
UNION ALL SELECT 'GENOME_FUNGIDB', count(*)::text FROM bio.genome WHERE source = 'fungidb'
UNION ALL SELECT 'PUBLICATIONS_CORE', count(*)::text FROM core.publications
UNION ALL SELECT 'TAXON_COMPOUND', count(*)::text FROM bio.taxon_compound
UNION ALL SELECT 'TAXON_TRAIT', count(*)::text FROM bio.taxon_trait
UNION ALL SELECT 'PUB_TAXON_LINK', count(*)::text FROM bio.publication_taxon;

SELECT table_schema, table_name FROM information_schema.tables
WHERE table_schema IN ('bio','core') AND table_name LIKE '%genet%' OR table_name LIKE '%public%' OR table_name LIKE '%media%' OR table_name LIKE '%compound%'
ORDER BY 1,2;

SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid = 'core.taxon'::regclass AND conname LIKE '%kingdom%';

SELECT grantee, privilege_type, table_schema, table_name
FROM information_schema.role_table_grants
WHERE table_schema = 'bio' AND grantee IN ('mindex','mycosoft')
ORDER BY table_name, grantee;

SELECT current_user, session_user;
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
    i, o, e = c.exec_command(cmd, timeout=90)
    print((o.read() or b"").decode())
    print((e.read() or b"").decode())
    # ETL container DB URL user
    i2, o2, e2 = c.exec_command(
        "sudo docker exec mindex-etl printenv MINDEX_DATABASE_URL 2>/dev/null | sed 's/:\\/\\/[^:]*:[^@]*@/:\\/\\/USER:***@/'"
    )
    print("ETL_DB_URL:", (o2.read() or b"").decode().strip())
    c.close()


if __name__ == "__main__":
    main()
