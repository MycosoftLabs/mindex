#!/usr/bin/env python3
"""Mount real NAS on MINDEX VM 189 and optionally migrate local library off VM disk."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_creds() -> None:
    for creds in (ROOT / ".credentials.local", ROOT.parent.parent / "MAS" / "mycosoft-mas" / ".credentials.local"):
        if creds.is_file():
            for line in creds.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            return


def main() -> int:
    load_creds()
    import paramiko

    host = os.environ.get("MINDEX_VM_HOST", "192.168.0.189")
    user = os.environ.get("VM_SSH_USER", "mycosoft")
    password = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    nas_pass = os.environ.get("NAS_SMB_PASSWORD") or os.environ.get("NAS_PASSWORD", "")
    if not password:
        print("VM_PASSWORD not set", file=sys.stderr)
        return 1

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {user}@{host}...")
    ssh.connect(host, username=user, password=password, timeout=30)

    remote_root = "/home/mycosoft/mindex"
    cifs_url = os.environ.get("NAS_CIFS_URL", "//192.168.0.105/mycosoft.com/mindex")
    smb_user = os.environ.get("NAS_SMB_USER", "mycosoft")
    script = (ROOT / "scripts" / "setup_mindex_nas_mount.sh").read_text(encoding="utf-8")
    sftp = ssh.open_sftp()
    remote_script = f"{remote_root}/scripts/setup_mindex_nas_mount.sh"
    _sftp_mkdirs(sftp, f"{remote_root}/scripts")
    with sftp.file(remote_script, "w") as f:
        f.write(script)
    sftp.close()

    nas_env = f"NAS_SMB_PASSWORD='{nas_pass}' " if nas_pass else ""
    vm_pass_esc = password.replace("'", "'\"'\"'")
    cmd = (
        f"cd {remote_root} && "
        f"export NAS_CIFS_URL='{cifs_url}' NAS_SMB_USER='{smb_user}' {nas_env}&& "
        f"echo '{vm_pass_esc}' | sudo -S -E bash scripts/setup_mindex_nas_mount.sh"
    )
    print(">>> Running NAS mount script on VM...")
    _, stdout, stderr = ssh.exec_command(cmd, timeout=3600)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    print(out[-8000:])
    if err:
        print(err[-3000:], file=sys.stderr)

    for tail_cmd in (
        f"grep -E '^NAS_MOUNT_PATH|^MINDEX_NAS_DATA_DIR' {remote_root}/.env || true",
        f"cd {remote_root} && docker compose restart api 2>&1 | tail -5",
        "sleep 8",
        f"cd {remote_root} && export $(grep -E '^MINDEX_INTERNAL' .env | xargs) && "
        "curl -sf -H \"X-Internal-Token: ${MINDEX_INTERNAL_TOKENS%%,*}\" "
        "http://127.0.0.1:8000/api/mindex/library/storage | head -c 600",
    ):
        print(f">>> {tail_cmd[:80]}")
        _, o, e = ssh.exec_command(tail_cmd, timeout=120)
        print(o.read().decode()[:1500])
        er = e.read().decode()
        if er:
            print(er[:500], file=sys.stderr)

    ssh.close()
    return 0


def _sftp_mkdirs(sftp, path: str) -> None:
    parts = path.strip("/").split("/")
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}" if cur else f"/{p}"
        try:
            sftp.stat(cur)
        except OSError:
            try:
                sftp.mkdir(cur)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
