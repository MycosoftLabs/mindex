#!/usr/bin/env python3
"""Deploy SINE acoustic stack to MINDEX VM 189 (SFTP + migration + deps)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Local paths to sync when git on VM is behind dev machine
SYNC_PATHS = [
    "pyproject.toml",
    "migrations/20260604_library_blob_labels_may27_2026.sql",
    "migrations/20260605_sine_acoustic_stack_may27_2026.sql",
    "mindex_api/main.py",
    "mindex_api/routers/__init__.py",
    "mindex_api/routers/sine_acoustic.py",
    "mindex_api/routers/library.py",
    "mindex_api/services/sine_acoustic",
    "tests/test_sine_acoustic_pipeline.py",
]


def load_creds() -> None:
    creds = ROOT / ".credentials.local"
    if not creds.is_file():
        mas = ROOT.parent.parent / "MAS" / "mycosoft-mas" / ".credentials.local"
        if mas.is_file():
            creds = mas
    if not creds.is_file():
        return
    for line in creds.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _sftp_mkdirs(sftp, remote_dir: str) -> None:
    remote_dir = remote_dir.replace("//", "/").rstrip("/")
    if not remote_dir:
        return
    parts = remote_dir.split("/")
    cur = ""
    for p in parts:
        if not p:
            continue
        cur = f"{cur}/{p}" if cur else p
        try:
            sftp.stat(cur)
        except OSError:
            try:
                sftp.mkdir(cur)
            except OSError:
                pass


def sftp_put_tree(sftp, local: Path, remote_path: str) -> int:
    count = 0
    if local.is_file():
        remote = remote_path.replace("//", "/")
        _sftp_mkdirs(sftp, "/".join(remote.rsplit("/", 1)[:-1]))
        sftp.put(str(local), remote)
        print(f"  put {local.relative_to(ROOT)} -> {remote}")
        return 1
    for item in sorted(local.rglob("*")):
        if item.is_dir() or "__pycache__" in item.parts:
            continue
        rel = item.relative_to(local)
        remote = f"{remote_path}/{rel.as_posix()}".replace("//", "/")
        _sftp_mkdirs(sftp, "/".join(remote.rsplit("/", 1)[:-1]))
        sftp.put(str(item), remote)
        count += 1
    if count:
        print(f"  put {local.relative_to(ROOT)}/ ({count} files)")
    return count


def main() -> int:
    load_creds()
    try:
        import paramiko
    except ImportError:
        print("pip install paramiko", file=sys.stderr)
        return 1

    host = os.environ.get("MINDEX_VM_HOST", "192.168.0.189")
    user = os.environ.get("VM_SSH_USER", "mycosoft")
    password = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    if not password:
        print("VM_PASSWORD not set", file=sys.stderr)
        return 1

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {user}@{host}...")
    ssh.connect(host, username=user, password=password, timeout=30)
    remote_root = "/home/mycosoft/mindex"

    def run(cmd: str, timeout: int = 600) -> tuple[int, str, str]:
        print(f">>> {cmd}")
        _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        code = stdout.channel.recv_exit_status()
        if out:
            print(out[-6000:])
        if err:
            print(err[-3000:], file=sys.stderr)
        return code, out, err

    run(f"cd {remote_root} && git pull --ff-only 2>/dev/null || true")

    run(
        f"mkdir -p {remote_root}/mindex_api/services/sine_acoustic "
        f"{remote_root}/mindex_api/routers",
    )
    print("SFTP sync SINE stack files...")
    sftp = ssh.open_sftp()
    uploaded = 0
    for rel in SYNC_PATHS:
        local = ROOT / rel
        if not local.exists():
            print(f"  skip missing {rel}")
            continue
        remote_path = f"{remote_root}/{rel}".replace("\\", "/")
        uploaded += sftp_put_tree(sftp, local, remote_path)
    sftp.close()
    print(f"Uploaded {uploaded} file(s)")

    for mig in (
        "migrations/20260604_library_blob_labels_may27_2026.sql",
        "migrations/20260605_sine_acoustic_stack_may27_2026.sql",
    ):
        run(
            f"cd {remote_root} && test -f {mig} && cat {mig} | docker exec -i mindex-postgres "
            f"psql -U mindex -d mindex -v ON_ERROR_STOP=1 2>&1 | tail -25",
            timeout=600,
        )

    run(
        f"cd {remote_root} && docker compose exec -T api pip install -q "
        f"'numpy==1.26.4' 'scipy==1.11.4' 'soundfile>=0.12' 'auditok>=0.2'",
        timeout=300,
    )
    run(f"cd {remote_root} && docker compose restart api", timeout=120)
    run("sleep 8")

    code, out, _ = run(
        f"cd {remote_root} && set -a && [ -f .env ] && . ./.env; set +a; "
        f'curl -sf -H "X-Internal-Token: ${{MINDEX_INTERNAL_TOKENS%%%,*}}" '
        f"http://127.0.0.1:8000/api/mindex/sine/status || "
        f'curl -sf -H "X-Internal-Token: $MINDEX_INTERNAL_SECRET" '
        f"http://127.0.0.1:8000/api/mindex/sine/status || echo SINE_STATUS_FAILED",
        timeout=60,
    )
    ssh.close()
    if "SINE_STATUS_FAILED" in out or code != 0:
        print("WARNING: sine/status check failed — inspect API logs", file=sys.stderr)
        return 1
    print("SINE deploy finished — status OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
