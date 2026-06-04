#!/usr/bin/env python3
"""Git pull MINDEX on VM and verify classify route exists."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_creds() -> None:
    for creds in (
        ROOT / ".credentials.local",
        ROOT.parent.parent / "MAS" / "mycosoft-mas" / ".credentials.local",
    ):
        if creds.is_file():
            for line in creds.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    load_creds()
    import paramiko

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=os.environ["VM_PASSWORD"], timeout=30)

    def run(cmd: str, t: int = 180) -> str:
        _, o, e = ssh.exec_command(cmd, timeout=t)
        return (o.read() + e.read()).decode("utf-8", errors="replace")

    print(run("cd /home/mycosoft/mindex && git pull --ff-only 2>&1 | tail -5", 120))
    print(run("docker restart mindex-api && sleep 10 && docker logs mindex-api --tail 5 2>&1"))
    print(run(
        "docker exec mindex-api pip install -q 'numpy>=1.26,<2' scipy soundfile auditok 2>&1 | tail -3",
        120,
    ))
    print(run(
        r"""cd /home/mycosoft/mindex && set -a && . ./.env && set +a
TOK="${MINDEX_INTERNAL_TOKENS%%,*}"
# pick smallest acoustic blob by size
BID=$(docker exec mindex-postgres psql -U mindex -d mindex -t -c \
  "SELECT id::text FROM library.blob WHERE category='acoustic' ORDER BY size_bytes ASC NULLS LAST LIMIT 1" | tr -d ' ')
echo "blob=$BID"
curl -s -m 120 -w "\nclassify:%{http_code}\n" -X POST -H "X-Internal-Token: $TOK" \
  "http://127.0.0.1:8000/api/mindex/library/blobs/${BID}/classify?detectors=frequency_fft,visualisation_sonic" | tail -20
""",
        180,
    ))
    ssh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
