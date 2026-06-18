#!/usr/bin/env python3
import os
import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8")

VM_PASS = os.environ.get("VM_PASSWORD", "")


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)

    diag = "docker exec mindex-postgres psql -U mindex -d mindex -c \"SELECT nspname, pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname IN ('obs','core');\""
    _, stdout, _ = ssh.exec_command(diag, timeout=30)
    print(stdout.read().decode())

    # Try granting as schema owner via SET ROLE if needed
    grants = (
        "ALTER SCHEMA obs OWNER TO mindex; "
        "ALTER SCHEMA core OWNER TO mindex; "
        "GRANT USAGE ON SCHEMA obs TO mindex; "
        "GRANT USAGE ON SCHEMA core TO mindex; "
        "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA obs TO mindex; "
        "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA core TO mindex; "
        "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA obs TO mindex; "
        "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA core TO mindex;"
    )
    # Run as mindex superuser in container (POSTGRES_USER=mindex)
    cmd = f'docker exec mindex-postgres psql -U mindex -d mindex -c "{grants}"'
    _, stdout, stderr = ssh.exec_command(cmd, timeout=60)
    print(stdout.read().decode())
    err = stderr.read().decode()
    if err:
        print("grant err:", err)

    _, stdout, stderr = ssh.exec_command(
        "docker exec mindex-postgres psql -U mindex -d mindex -c \"SELECT count(*) FROM obs.observation;\"",
        timeout=30,
    )
    print(stdout.read().decode())
    print(stderr.read().decode())

    ssh.exec_command("docker restart mindex-api", timeout=60)
    time.sleep(5)
    _, stdout, _ = ssh.exec_command(
        "set -a; . /home/mycosoft/mindex/.env; set +a; curl -s 'http://localhost:8000/api/mindex/observations?limit=2' -H 'X-API-Key: $MINDEX_API_KEY'",
        timeout=20,
    )
    print("api:", stdout.read().decode()[:400])
    ssh.close()


if __name__ == "__main__":
    main()
