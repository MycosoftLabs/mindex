#!/usr/bin/env python3
import os
import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8")

VM_PASS = os.environ.get("VM_PASSWORD", "")


def run(ssh, cmd: str, timeout: int = 60) -> tuple[str, str]:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode(), stderr.read().decode()


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)

    out, _ = run(ssh, "docker exec mindex-postgres env")
    for line in out.splitlines():
        if any(k in line.upper() for k in ("POSTGRES", "PG", "USER", "PASS", "DB")):
            print(line)

    out, _ = run(ssh, "docker exec mindex-api env")
    for line in out.splitlines():
        if any(k in line.upper() for k in ("DATABASE", "POSTGRES", "DB_", "SQL")):
            print("api:", line)

    # List roles
    out, err = run(
        ssh,
        "docker exec mindex-postgres psql -U mindex -d mindex -c \"SELECT rolname, rolsuper FROM pg_roles ORDER BY 1;\"",
    )
    print(out)
    if err:
        print(err)

    # Try mycosoft role if exists
    out, err = run(
        ssh,
        "docker exec mindex-postgres psql -U mycosoft -d mindex -c \"SELECT 1;\" 2>&1",
    )
    print("mycosoft login:", out, err)

    # Try with MINDEX_DB_PASSWORD from common env
    db_pass = os.environ.get("MINDEX_DB_PASSWORD", "")
    if db_pass:
        grants = (
            "GRANT USAGE ON SCHEMA obs TO mindex; "
            "GRANT USAGE ON SCHEMA core TO mindex; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA obs TO mindex; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO mindex; "
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA obs TO mindex; "
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO mindex; "
            "ALTER DEFAULT PRIVILEGES IN SCHEMA obs GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mindex; "
            "ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mindex;"
        )
        # Use PGPASSWORD for mycosoft
        cmd = (
            f"docker exec -e PGPASSWORD='{db_pass}' mindex-postgres "
            f"psql -U mycosoft -d mindex -c \"{grants}\""
        )
        out, err = run(ssh, cmd)
        print("grants as mycosoft:", out, err)

    out, err = run(
        ssh,
        "docker exec mindex-postgres psql -U mindex -d mindex -c \"SELECT has_schema_privilege('mindex','obs','USAGE'), count(*) FROM obs.observation;\"",
    )
    print(out, err)

    run(ssh, "docker restart mindex-api")
    time.sleep(6)
    out, _ = run(
        ssh,
        "set -a; . /home/mycosoft/mindex/.env; set +a; curl -s 'http://localhost:8000/api/mindex/observations?limit=2' -H 'X-API-Key: $MINDEX_API_KEY'",
    )
    print("api sample:", out[:500])

    ssh.close()


if __name__ == "__main__":
    main()
