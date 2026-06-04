#!/usr/bin/env python3
"""Mount NAS on 189, migrate library, apply migrations, start API, verify SINE."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_creds() -> None:
    for creds in (ROOT / ".credentials.local", ROOT.parent.parent / "MAS" / "mycosoft-mas" / ".credentials.local"):
        if creds.is_file():
            for line in creds.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def ssh_run(ssh, cmd: str, timeout: int = 120) -> tuple[int, str]:
    print(f">>> {cmd[:140]}{'...' if len(cmd) > 140 else ''}")
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    channel = stdout.channel
    deadline = time.time() + timeout
    chunks: list[str] = []
    while not channel.exit_status_ready():
        if channel.recv_ready():
            chunks.append(channel.recv(4096).decode(errors="replace"))
        elif time.time() > deadline:
            print("WARN: command timed out waiting for exit", file=sys.stderr)
            break
        else:
            time.sleep(0.5)
    if channel.recv_ready():
        chunks.append(channel.recv(65536).decode(errors="replace"))
    out = "".join(chunks)
    code = channel.recv_exit_status() if channel.exit_status_ready() else -1
    err = stderr.read().decode(errors="replace")
    if out:
        print(out[-4000:])
    if err:
        print(err[-1500:], file=sys.stderr)
    return code, out


def main() -> int:
    load_creds()
    import paramiko

    vm_pass = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    nas_pass = os.environ.get("NAS_SMB_PASSWORD") or os.environ.get("NAS_PASSWORD", "")
    nas_user = os.environ.get("NAS_SMB_USER", "morgan")
    if not vm_pass or not nas_pass:
        print("VM_PASSWORD and NAS_SMB_PASSWORD required", file=sys.stderr)
        return 1

    host = os.environ.get("MINDEX_VM_HOST", "192.168.0.189")
    remote_root = "/home/mycosoft/mindex"
    backup = "/var/lib/mindex-nas-local-backup-20260604005520"

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to mycosoft@{host}...")
    ssh.connect(host, username="mycosoft", password=vm_pass, timeout=30)

    vm_esc = vm_pass.replace("'", "'\"'\"'")

    # Sync latest API code (git on VM often behind)
    sync_paths = [
        "mindex_api/main.py",
        "mindex_api/routers/__init__.py",
        "mindex_api/routers/sine_acoustic.py",
        "mindex_api/routers/library.py",
        "mindex_api/services/sine_acoustic",
    ]
    sftp = ssh.open_sftp()

    def sftp_mkdirs(remote_dir: str) -> None:
        parts = remote_dir.strip("/").split("/")
        cur = ""
        for p in parts:
            cur = f"{cur}/{p}" if cur else p
            try:
                sftp.stat(cur)
            except OSError:
                try:
                    sftp.mkdir(cur)
                except OSError:
                    pass

    for rel in sync_paths:
        local = ROOT / rel
        if not local.exists():
            continue
        remote_base = f"{remote_root}/{rel}".replace("\\", "/")
        if local.is_file():
            sftp_mkdirs("/".join(remote_base.rsplit("/", 1)[:-1]))
            sftp.put(str(local), remote_base)
        else:
            for item in local.rglob("*"):
                if item.is_dir() or "__pycache__" in item.parts:
                    continue
                r = f"{remote_base}/{item.relative_to(local).as_posix()}"
                sftp_mkdirs("/".join(r.rsplit("/", 1)[:-1]))
                sftp.put(str(item), r)
    sftp.close()
    print("SFTP sync done")

    # Cleanup + disk
    for c in (
        "rm -rf /home/mycosoft/mindex/C: /home/mycosoft/.claude/downloads 2>/dev/null; true",
        f"echo '{vm_esc}' | sudo -S journalctl --vacuum-size=80M 2>/dev/null || true",
        "df -h / /mnt/nas/mindex 2>/dev/null | head -4",
    ):
        ssh_run(ssh, c, timeout=90)

    import base64

    creds_plain = f"username={nas_user}\npassword={nas_pass}\ndomain=WORKGROUP\n"
    creds_b64 = base64.b64encode(creds_plain.encode()).decode()
    ssh_run(
        ssh,
        f"echo '{creds_b64}' | base64 -d > /tmp/mindex-nas.creds && "
        f"echo '{vm_esc}' | sudo -S mv /tmp/mindex-nas.creds /etc/samba/mycosoft-nas.creds && "
        f"echo '{vm_esc}' | sudo -S chmod 600 /etc/samba/mycosoft-nas.creds",
        timeout=30,
    )

    share_urls = [
        os.environ.get("NAS_CIFS_URL", "//192.168.0.105/mycosoft.com/mindex"),
        "//192.168.0.105/mycosoft/mindex",
        "//192.168.0.105/mycosoft.com",
        "//192.168.0.105/mindex",
    ]
    mounted = False
    for url in share_urls:
        ssh_run(ssh, f"echo '{vm_esc}' | sudo -S umount /mnt/nas/mindex 2>/dev/null || true", timeout=30)
        ssh_run(ssh, f"echo '{vm_esc}' | sudo -S mkdir -p /mnt/nas/mindex", timeout=30)
        code, out = ssh_run(
            ssh,
            f"echo '{vm_esc}' | sudo -S mount -t cifs '{url}' /mnt/nas/mindex "
            f"-o credentials=/etc/samba/mycosoft-nas.creds,uid=1000,gid=1000,vers=3.0,iocharset=utf8 2>&1",
            timeout=60,
        )
        _, check = ssh_run(ssh, "findmnt -n -o FSTYPE,SOURCE /mnt/nas/mindex 2>/dev/null; df -h /mnt/nas/mindex 2>/dev/null | tail -1", timeout=30)
        if "cifs" in check.lower() or "smb" in check.lower():
            print(f"NAS mounted via {url}")
            mounted = True
            break
        if "denied" in out.lower() or "error" in out.lower():
            print(f"Mount failed for {url}")

    if not mounted:
        print("CIFS mount failed for all share URLs", file=sys.stderr)
        ssh.close()
        return 1

    ssh_run(
        ssh,
        f"echo '{vm_esc}' | sudo -S mkdir -p /mnt/nas/mindex/Library/acoustic && "
        f"echo '{vm_esc}' | sudo -S chown -R mycosoft:mycosoft /mnt/nas/mindex 2>/dev/null || true",
        timeout=60,
    )

    # Rsync local backup to NAS (background)
    ssh_run(
        ssh,
        f"test -d {backup}/Library && nohup rsync -a {backup}/Library/ /mnt/nas/mindex/Library/ "
        f"> /tmp/mindex-nas-rsync.log 2>&1 & echo RSYNC_PID=$! || echo NO_BACKUP",
        timeout=30,
    )

    # Ensure .env NAS paths
    ssh_run(
        ssh,
        f"cd {remote_root} && grep -q NAS_MOUNT_PATH .env 2>/dev/null || "
        f"echo 'NAS_MOUNT_PATH=/mnt/nas/mindex' >> .env; "
        f"grep -q MINDEX_NAS_DATA_DIR .env 2>/dev/null || "
        f"echo 'MINDEX_NAS_DATA_DIR=/mnt/nas/mindex' >> .env",
        timeout=30,
    )

    # Migrations in background (avoid Paramiko timeout)
    for mig in (
        "migrations/20260604_library_blob_labels_may27_2026.sql",
        "migrations/20260605_sine_acoustic_stack_may27_2026.sql",
    ):
        log = f"/tmp/mindex-mig-{Path(mig).stem}.log"
        ssh_run(
            ssh,
            f"cd {remote_root} && nohup bash -c 'cat {mig} | docker exec -i mindex-postgres psql "
            f"-U mindex -d mindex -v ON_ERROR_STOP=1 > {log} 2>&1; echo EXIT:$? >> {log}' "
            f"> /dev/null 2>&1 & echo STARTED:{log}",
            timeout=30,
        )

    print("Waiting for migrations (up to 180s)...")
    for _ in range(36):
        time.sleep(5)
        _, logs = ssh_run(ssh, "tail -3 /tmp/mindex-mig-*.log 2>/dev/null; grep -l 'EXIT:0' /tmp/mindex-mig-*.log 2>/dev/null | wc -l", timeout=20)
        if logs.strip().endswith("2") or "EXIT:0" in logs and logs.count("EXIT:0") >= 2:
            break

    ssh_run(ssh, "tail -20 /tmp/mindex-mig-20260604*.log /tmp/mindex-mig-20260605*.log 2>/dev/null", timeout=30)

    # Start API + deps
    ssh_run(ssh, f"cd {remote_root} && docker compose up -d api 2>&1 | tail -8", timeout=180)
    ssh_run(
        ssh,
        f"cd {remote_root} && docker compose exec -T api pip install -q "
        f"'numpy==1.26.4' 'scipy==1.11.4' 'soundfile>=0.12' 'auditok>=0.2' 2>&1 | tail -6",
        timeout=300,
    )
    ssh_run(ssh, f"cd {remote_root} && docker compose restart api 2>&1 | tail -5", timeout=120)
    ssh_run(ssh, "sleep 12", timeout=20)

    token_cmd = (
        f"cd {remote_root} && set -a && . ./.env 2>/dev/null; set +a; "
        f'TOK="${{MINDEX_INTERNAL_TOKENS%%,*}}"; '
        f'[ -z "$TOK" ] && TOK="$MINDEX_INTERNAL_SECRET"; '
        f'curl -sf -m 15 -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/api/mindex/health; echo; '
        f'curl -sf -m 15 -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/api/mindex/library/storage; echo; '
        f'curl -sf -m 15 -H "X-Internal-Token: $TOK" http://127.0.0.1:8000/api/mindex/sine/status; echo'
    )
    code, out = ssh_run(ssh, token_cmd, timeout=60)
    ssh.close()

    if "remote_nas" in out and "true" in out.lower():
        print("=== library/storage shows remote_nas ===")
    if "sine" in out.lower() or "detector" in out.lower():
        print("=== sine/status OK ===")
    if code != 0 or "curl:" in out.lower() and out.count("curl:") >= 3:
        print("Some health checks failed — inspect docker logs on VM", file=sys.stderr)
        return 1
    print("=== finish_mindex_nas_and_sine complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
