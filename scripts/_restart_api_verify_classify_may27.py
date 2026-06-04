#!/usr/bin/env python3
"""Restart standalone mindex-api and verify classify route."""
from __future__ import annotations

import json
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

    print(run("docker restart mindex-api && sleep 14"))
    print(run("docker logs mindex-api --tail 20 2>&1"))
    print(run("docker exec mindex-api pip install -q numpy scipy soundfile auditok 2>&1 | tail -5", 120))

    out = run(
        r"""cd /home/mycosoft/mindex && set -a && . ./.env && set +a
TOK="${MINDEX_INTERNAL_TOKENS%%,*}"
curl -sf -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/api/mindex/health
echo
curl -sf -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/openapi.json -o /tmp/oapi.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/oapi.json"))
paths=[p for p in d.get("paths",{}) if "classify" in p]
print("classify_paths:", paths)
PY
BID=$(docker exec mindex-postgres psql -U mindex -d mindex -t -c \
  "SELECT id::text FROM library.blob WHERE category='acoustic' ORDER BY size_bytes ASC NULLS LAST LIMIT 1" | tr -d ' ')
echo "blob=$BID"
curl -s -m 120 -w "\nclassify_http:%{http_code}\n" -X POST -H "X-Internal-Token: $TOK" \
  "http://127.0.0.1:8000/api/mindex/library/blobs/${BID}/classify?detectors=frequency_fft" | tail -15
""",
        200,
    )
    print(out)
    ssh.close()
    if "classify_http:200" in out or "classify_http:201" in out:
        return 0
    if "classify_paths:" in out and "/classify" in out and "classify_http:404" not in out:
        return 0
    return 1 if "classify_http:404" in out else 0


if __name__ == "__main__":
    sys.exit(main())
