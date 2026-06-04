#!/usr/bin/env python3
"""Set MINDEX_DB_HOST=mindex-postgres in .env and restart API (fixes db connection refused)."""
from __future__ import annotations

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


def set_env_key(content: str, key: str, value: str) -> str:
    lines = content.splitlines()
    out = []
    found = False
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"


def main() -> int:
    load_creds()
    import paramiko

    vm = os.environ["VM_PASSWORD"]
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username="mycosoft", password=vm, timeout=30)

    def run(cmd: str, t: int = 120) -> str:
        _, o, e = ssh.exec_command(cmd, timeout=t)
        return (o.read() + e.read()).decode("utf-8", errors="replace")

    env_path = "/home/mycosoft/mindex/.env"
    sftp = ssh.open_sftp()
    try:
        with sftp.open(env_path, "r") as f:
            content = f.read().decode("utf-8", errors="replace")
    except OSError:
        content = ""

    for key, val in (
        ("MINDEX_DB_HOST", "mindex-postgres"),
        ("MINDEX_DB_PORT", "5432"),
        ("MINDEX_DB_USER", "mindex"),
        ("MINDEX_DB_PASSWORD", "mindex"),
        ("MINDEX_DB_NAME", "mindex"),
        ("DATABASE_URL", "postgresql://mindex:mindex@mindex-postgres:5432/mindex"),
        ("MINDEX_DATABASE_URL", "postgresql://mindex:mindex@mindex-postgres:5432/mindex"),
    ):
        content = set_env_key(content, key, val)

    with sftp.open(env_path, "w") as f:
        f.write(content.encode("utf-8"))
    sftp.close()

    print(run("grep -E '^MINDEX_DB_' /home/mycosoft/mindex/.env | head -8"))

    recreate = """
cd /home/mycosoft/mindex
NET=mindex_mindex-network
docker rm -f mindex-api 2>/dev/null || true
docker run -d --name mindex-api --restart unless-stopped \\
  --network "$NET" \\
  -p 0.0.0.0:8000:8000 \\
  -v /home/mycosoft/mindex/mindex_api:/app/mindex_api \\
  -v /home/mycosoft/mindex/mindex_etl:/app/mindex_etl \\
  -v /mnt/nas/mindex:/mnt/nas/mindex:ro \\
  --env-file /home/mycosoft/mindex/.env \\
  mindex-etl:latest \\
  uvicorn mindex_api.main:app --host 0.0.0.0 --port 8000
"""
    print(run(recreate, 180))
    print(run("sleep 8"))

    verify = run(
        r"""cd /home/mycosoft/mindex && set -a && . ./.env && set +a
TOK="${MINDEX_INTERNAL_TOKENS%%,*}"
curl -s -m 10 http://127.0.0.1:8000/api/mindex/health; echo
curl -s -m 20 -w "\nblobs:%{http_code}\n" -H "X-Internal-Token: $TOK" \
  "http://127.0.0.1:8000/api/mindex/library/blobs?category=acoustic&limit=2" | tail -6
curl -s -m 10 -w "\nsine:%{http_code}\n" -H "X-Internal-Token: $TOK" \
  http://127.0.0.1:8000/api/mindex/sine/status | tail -3
echo "${MINDEX_INTERNAL_TOKENS%%,*}"
""",
        60,
    )
    print(verify)
    tok = verify.strip().splitlines()[-1].strip() if verify else ""
    ssh.close()

    website_env = Path(r"D:\Users\admin2\Desktop\MYCOSOFT\CODE\WEBSITE\website\.env.local")
    if tok and len(tok) > 20 and website_env.is_file():
        text = website_env.read_text(encoding="utf-8")
        website_env.write_text(
            set_env_key(
                set_env_key(
                    set_env_key(text, "MINDEX_INTERNAL_TOKEN", tok),
                    "MINDEX_API_URL",
                    f"http://{HOST}:8000",
                ),
                "MINDEX_API_BASE_URL",
                f"http://{HOST}:8000",
            ),
            encoding="utf-8",
        )
        print("website .env.local token synced")

    import urllib.request

    for label, path in (
        ("health", "/api/mindex/health"),
        ("blobs", "/api/mindex/library/blobs?category=acoustic&limit=2"),
        ("sine", "/api/mindex/sine/status"),
        ("storage", "/api/mindex/library/storage"),
    ):
        headers = {"X-Internal-Token": tok} if tok and path != "/api/mindex/health" else {}
        req = urllib.request.Request(f"http://{HOST}:8000{path}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                print(f"DEV {label}: {resp.status} {body[:280]}")
        except Exception as exc:
            print(f"DEV {label} FAIL: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
