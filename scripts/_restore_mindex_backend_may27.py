#!/usr/bin/env python3
"""Full MINDEX 189 backend restore: postgres, API LAN, DB URL, sine deps, verify."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = "192.168.0.189"


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

    vm = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    if not vm:
        print("ERROR: VM_PASSWORD not set")
        return 1

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="mycosoft", password=vm, timeout=30)
    ve = vm.replace("'", "'\"'\"'")

    def run(cmd: str, t: int = 180) -> str:
        _, o, e = ssh.exec_command(cmd, timeout=t)
        out = (o.read() + e.read()).decode("utf-8", errors="replace")
        return out

    print("=== Docker state ===")
    print(run("docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | head -20"))

    print("=== Start postgres if down ===")
    print(run(
        "docker start mindex-postgres 2>/dev/null || "
        "docker compose -f /home/mycosoft/mindex/docker-compose.yml up -d mindex-postgres 2>/dev/null || "
        "cd /home/mycosoft/mindex && docker compose up -d postgres mindex-postgres 2>/dev/null; "
        "sleep 3; docker ps | grep -i postgres || true",
        120,
    ))

    print("=== Fix .env database + NAS paths ===")
    print(run(
        r"""cd /home/mycosoft/mindex
grep -q '^DATABASE_URL=' .env 2>/dev/null && \
  sed -i 's|^DATABASE_URL=.*|DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex|' .env || \
  echo 'DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex' >> .env
grep -q '^MINDEX_DATABASE_URL=' .env 2>/dev/null && \
  sed -i 's|^MINDEX_DATABASE_URL=.*|MINDEX_DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex|' .env || \
  echo 'MINDEX_DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex' >> .env
grep -q '^NAS_MOUNT_PATH=' .env 2>/dev/null || echo 'NAS_MOUNT_PATH=/mnt/nas/mindex' >> .env
grep -E '^DATABASE_URL=|^NAS_MOUNT' .env | head -5
mountpoint /mnt/nas/mindex 2>/dev/null || echo 'NAS not mounted'
""",
        60,
    ))

    print("=== Recreate mindex-api ===")
    recreate = f"""
set -e
cd /home/mycosoft/mindex
NET=$(docker inspect mindex-postgres -f '{{{{range $k,$v := .NetworkSettings.Networks}}}}{{{{$k}}}}{{{{end}}}}' 2>/dev/null || true)
if [ -z "$NET" ]; then
  NET=$(docker network ls --format '{{{{.Name}}}}' | grep -E 'mindex|default' | head -1)
fi
echo "network=$NET"
docker rm -f mindex-api 2>/dev/null || true
docker run -d --name mindex-api --restart unless-stopped \\
  --network "$NET" \\
  -p 0.0.0.0:8000:8000 \\
  -v /home/mycosoft/mindex/mindex_api:/app/mindex_api \\
  -v /home/mycosoft/mindex/mindex_etl:/app/mindex_etl \\
  -v /mnt/nas/mindex:/mnt/nas/mindex:ro \\
  --env-file /home/mycosoft/mindex/.env \\
  -e DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex \\
  -e MINDEX_DATABASE_URL=postgresql://mindex:mindex@mindex-postgres:5432/mindex \\
  mindex-etl:latest \\
  sh -c 'pip install -q "numpy>=1.26,<2" "scipy>=1.11" soundfile auditok 2>/dev/null; cd /app && uvicorn mindex_api.main:app --host 0.0.0.0 --port 8000'
"""
    print(run(recreate, 240))

    print("=== Firewall ===")
    for rule in ("ufw allow from 192.168.0.0/24 to any port 8000 proto tcp", "ufw allow 8000/tcp"):
        print(run(f"echo '{ve}' | sudo -S {rule} 2>&1", 30))

    print("=== Wait + logs ===")
    print(run("sleep 12; docker logs mindex-api --tail 15 2>&1"))
    print(run("ss -lntp | grep 8000 || true"))

    print("=== VM verify ===")
    verify = run(
        r"""cd /home/mycosoft/mindex && set -a && . ./.env && set +a
TOK="${MINDEX_INTERNAL_TOKENS%%,*}"
TOK="${TOK:-$MINDEX_INTERNAL_SERVICE_TOKEN}"
echo "token_len=${#TOK}"
curl -s -m 15 -w "\nhealth_http=%{http_code}\n" http://127.0.0.1:8000/api/mindex/health | tail -3
curl -s -m 20 -w "\nblobs_http=%{http_code}\n" -H "X-Internal-Token: $TOK" \
  "http://127.0.0.1:8000/api/mindex/library/blobs?category=acoustic&limit=2" | tail -5
curl -s -m 10 -w "\nstorage_http=%{http_code}\n" -H "X-Internal-Token: $TOK" \
  http://127.0.0.1:8000/api/mindex/library/storage | tail -3
curl -s -m 10 -w "\nsine_http=%{http_code}\n" -H "X-Internal-Token: $TOK" \
  http://127.0.0.1:8000/api/mindex/sine/status | tail -3
""",
        90,
    )
    print(verify[:4000])

    # Extract token for local verify file
    tok_line = run(
        r'cd /home/mycosoft/mindex && set -a && . ./.env && set +a && echo "${MINDEX_INTERNAL_TOKENS%%,*}"',
        15,
    ).strip()
    ssh.close()

    result = {"host": HOST, "token_prefix_len": len(tok_line), "vm_verify": verify[-1500:]}
    out_path = ROOT / "scripts" / "_restore_mindex_result.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")

    # Dev PC verify
    try:
        import urllib.request

        req = urllib.request.Request(f"http://{HOST}:8000/api/mindex/health")
        with urllib.request.urlopen(req, timeout=12) as resp:
            print(f"\nDEV health: {resp.status} {resp.read()[:200]}")
    except Exception as exc:
        print(f"\nDEV health FAIL: {exc}")

    if tok_line:
        try:
            import urllib.request

            req = urllib.request.Request(
                f"http://{HOST}:8000/api/mindex/library/blobs?category=acoustic&limit=2",
                headers={"X-Internal-Token": tok_line},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                print(f"DEV blobs: {resp.status} {body[:300]}")
        except Exception as exc:
            print(f"DEV blobs FAIL: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
