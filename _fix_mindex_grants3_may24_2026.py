#!/usr/bin/env python3
import os
import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8")

VM_PASS = os.environ.get("VM_PASSWORD", "")

GRANTS = """
GRANT USAGE ON SCHEMA obs TO mindex;
GRANT USAGE ON SCHEMA core TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA obs TO mindex;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO mindex;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA obs TO mindex;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO mindex;
ALTER DEFAULT PRIVILEGES FOR ROLE mycosoft IN SCHEMA obs GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mindex;
ALTER DEFAULT PRIVILEGES FOR ROLE mycosoft IN SCHEMA core GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mindex;
"""

BACKFILL = """
UPDATE obs.observation o
SET location = ST_SetSRID(ST_MakePoint(
  COALESCE((o.metadata->>'longitude')::float, (o.metadata->>'lng')::float),
  COALESCE((o.metadata->>'latitude')::float, (o.metadata->>'lat')::float)
), 4326)
WHERE o.location IS NULL
  AND (
    (o.metadata ? 'latitude' AND o.metadata ? 'longitude')
    OR (o.metadata ? 'lat' AND o.metadata ? 'lng')
  )
  AND COALESCE((o.metadata->>'latitude')::float, (o.metadata->>'lat')::float) BETWEEN -90 AND 90
  AND COALESCE((o.metadata->>'longitude')::float, (o.metadata->>'lng')::float) BETWEEN -180 AND 180;
"""


def run(ssh, cmd: str, timeout: int = 120) -> tuple[str, str]:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    return stdout.read().decode(), stderr.read().decode()


def psql_as_mycosoft(ssh, sql: str) -> tuple[str, str]:
    escaped = sql.replace('"', '\\"').replace("\n", " ")
    cmd = f'docker exec mindex-postgres psql -U mycosoft -d mindex -c "{escaped}"'
    return run(ssh, cmd, timeout=180)


def main() -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=VM_PASS, timeout=30)

    print("Applying grants as mycosoft...")
    out, err = psql_as_mycosoft(ssh, GRANTS)
    print(out)
    if err:
        print("grant stderr:", err)

    out, err = run(
        ssh,
        "docker exec mindex-postgres psql -U mindex -d mindex -c "
        "\"SELECT has_schema_privilege('mindex','obs','USAGE');\"",
    )
    print("usage privilege:", out.strip(), err.strip())

    print("Backfilling location from metadata...")
    out, err = psql_as_mycosoft(ssh, BACKFILL)
    print(out)
    if err:
        print("backfill stderr:", err)

    sd_bbox = "-117.6,32.5,-116.0,33.5"
    out, err = run(
        ssh,
        f"set -a; . /home/mycosoft/mindex/.env; set +a; curl -s 'http://localhost:8000/api/mindex/observations?bbox={sd_bbox}&kingdom=Fungi&limit=5' "
        "-H 'X-API-Key: $MINDEX_API_KEY'",
    )
    print("SD fungi sample (before restart):", out[:600])

    run(ssh, "docker restart mindex-api")
    time.sleep(8)

    out, err = run(
        ssh,
        "set -a; . /home/mycosoft/mindex/.env; set +a; curl -s 'http://localhost:8000/api/mindex/observations?limit=2' -H 'X-API-Key: $MINDEX_API_KEY'",
    )
    print("observations limit=2:", out[:600], err)

    out, _ = run(
        ssh,
        f"set -a; . /home/mycosoft/mindex/.env; set +a; curl -s 'http://localhost:8000/api/mindex/observations?bbox={sd_bbox}&kingdom=Fungi&limit=20' "
        "-H 'X-API-Key: $MINDEX_API_KEY'",
    )
    print("SD fungi limit=20:", out[:800])

    ssh.close()


if __name__ == "__main__":
    main()
