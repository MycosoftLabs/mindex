#!/usr/bin/env python3
"""Pull latest MINDEX on VM 189, migrate, restart API, verify health."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = "192.168.0.189"
SMALL_BLOB = "a742bbd6-383d-4a7f-8945-e3c7d55c1982"


def load_creds() -> None:
    for creds in (
        ROOT / ".credentials.local",
        ROOT.parent.parent / "MAS" / "mycosoft-mas" / ".credentials.local",
    ):
        if creds.is_file():
            for line in creds.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    load_creds()
    import paramiko

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="mycosoft", password=os.environ["VM_PASSWORD"], timeout=30)

    def run(cmd: str, t: int = 300) -> str:
        _, o, e = ssh.exec_command(cmd, timeout=t)
        out = (o.read() + e.read()).decode("utf-8", errors="replace")
        print(out[-8000:] if len(out) > 8000 else out)
        return out

    print("=== git pull ===")
    run("cd /home/mycosoft/mindex && git fetch origin && git reset --hard origin/main && git log -1 --oneline")

    print("=== ensure DB host in .env ===")
    run(
        r"""python3 - <<'PY'
from pathlib import Path
p = Path("/home/mycosoft/mindex/.env")
lines = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []
keys = {
    "MINDEX_DB_HOST": "mindex-postgres",
    "MINDEX_DB_PORT": "5432",
    "MINDEX_DB_USER": "mindex",
    "MINDEX_DB_NAME": "mindex",
    "DATABASE_URL": "postgresql://mindex:mindex@mindex-postgres:5432/mindex",
    "MINDEX_DATABASE_URL": "postgresql://mindex:mindex@mindex-postgres:5432/mindex",
}
out, seen = [], set()
for line in lines:
    if "=" in line and not line.strip().startswith("#"):
        k = line.split("=", 1)[0].strip()
        if k in keys:
            seen.add(k)
            continue
    out.append(line)
for k, v in keys.items():
    if k not in seen:
        out.append(f"{k}={v}")
    else:
        out.insert(0, f"{k}={v}")
p.write_text("\n".join(dict.fromkeys(out)) + "\n", encoding="utf-8")
print("env ok")
PY""",
        60,
    )

    migs = (
        "migrations/20260527_library_acoustic_may27_2026.sql",
        "migrations/20260604_library_blob_labels_may27_2026.sql",
        "migrations/20260605_sine_acoustic_stack_may27_2026.sql",
    )
    for mig in migs:
        print(f"=== migration {mig} ===")
        run(
            f"cd /home/mycosoft/mindex && test -f {mig} && "
            f"cat {mig} | docker exec -i mindex-postgres "
            f"psql -U mindex -d mindex -v ON_ERROR_STOP=0 2>&1 | tail -8",
            120,
        )

    print("=== pip deps + restart API ===")
    run("docker exec mindex-api pip install -q 'numpy>=1.26,<2' scipy soundfile auditok 2>&1 | tail -3", 180)
    run("docker restart mindex-api && sleep 14 && docker ps --filter name=mindex-api --format '{{.Status}}'")

    print("=== VM curl checks ===")
    run(
        r"""cd /home/mycosoft/mindex && set -a && . ./.env && set +a
TOK="${MINDEX_INTERNAL_TOKENS%%,*}"
curl -sf -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/api/mindex/health
echo
curl -sf -H "X-Internal-Token: $TOK" "http://127.0.0.1:8000/api/mindex/library/blobs?category=acoustic&limit=2" | python3 -c "import sys,json;d=json.load(sys.stdin);print('blobs',d.get('total'))"
curl -sf -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/api/mindex/sine/status | python3 -c "import sys,json;d=json.load(sys.stdin);print('sine',d.get('acoustic_blobs'))"
curl -s -m 90 -w "\nclassify:%{http_code}\n" -X POST -H "X-Internal-Token: $TOK" \
  "http://127.0.0.1:8000/api/mindex/library/blobs/""" + SMALL_BLOB + r"""/classify?detectors=frequency_fft" | tail -3
curl -s -m 90 -w "\nanalyze:%{http_code}\n" -X POST -H "X-Internal-Token: $TOK" \
  "http://127.0.0.1:8000/api/mindex/sine/blobs/""" + SMALL_BLOB + r"""/analyze?detectors=frequency_fft" | tail -3
""",
        200,
    )

    ssh.close()

    # LAN check from dev PC
    tok = (os.environ.get("MINDEX_INTERNAL_TOKENS") or "").split(",")[0].strip()
    if not tok:
        for line in Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\WEBSITE\website\.env.local").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if line.startswith("MINDEX_INTERNAL_TOKEN="):
                tok = line.split("=", 1)[1].strip()
    base = f"http://{HOST}:8000"
    checks = [
        f"{base}/api/mindex/health",
        f"{base}/api/mindex/library/blobs?category=acoustic&limit=2",
        f"{base}/api/mindex/sine/status",
    ]
    print("=== LAN from dev PC ===")
    ok = True
    for url in checks:
        req = urllib.request.Request(url, headers={"X-Internal-Token": tok})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                print(url, r.status)
        except urllib.error.HTTPError as e:
            print(url, "FAIL", e.code)
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
