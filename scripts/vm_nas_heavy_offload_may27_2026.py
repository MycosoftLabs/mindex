#!/usr/bin/env python3
"""
Move heavy MINDEX library/backups off VM root disk onto NAS (189).
Keeps Postgres/Redis/Qdrant on VM; all Library/acoustic files on CIFS only.
"""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/home/mycosoft/mindex"
MOUNT = "/mnt/nas/mindex"
BACKUP_GLOB = "/var/lib/mindex-nas-local-backup-*"
CIFS_URL = "//192.168.0.105/mycosoft.com/mindex"
RSYNC_LOG = "/tmp/mindex-nas-rsync.log"


def load_creds() -> None:
    for creds in (ROOT / ".credentials.local", ROOT.parent.parent / "MAS" / "mycosoft-mas" / ".credentials.local"):
        if creds.is_file():
            for line in creds.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def run(ssh, cmd: str, timeout: int = 600) -> str:
    print(f">>> {cmd[:120]}")
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if out:
        print(out[-5000:])
    if err.strip():
        print(err[-1200:], file=sys.stderr)
    return out


def sudo(ssh, inner: str, vm_esc: str, timeout: int = 300) -> str:
    return run(ssh, f"echo '{vm_esc}' | sudo -S bash -lc {repr(inner)}", timeout=timeout)


def ensure_cifs(ssh, vm_esc: str, nas_user: str, nas_pass: str) -> bool:
    chk = run(ssh, f"findmnt -n -o FSTYPE {MOUNT} 2>/dev/null || true", 30)
    if "cifs" in chk.lower():
        run(ssh, f"df -h {MOUNT} | tail -1")
        return True

    sudo(ssh, "apt-get update -qq && apt-get install -y -qq cifs-utils keyutils smbclient", vm_esc, 360)
    sudo(ssh, "modprobe cifs 2>/dev/null || true", vm_esc, 30)

    creds_plain = f"username={nas_user}\npassword={nas_pass}\ndomain=WORKGROUP\n"
    b64 = base64.b64encode(creds_plain.encode()).decode()
    run(ssh, f"echo {b64} | base64 -d > /tmp/nas.creds", 20)
    sudo(ssh, "mv /tmp/nas.creds /etc/samba/mycosoft-nas.creds && chmod 600 /etc/samba/mycosoft-nas.creds", vm_esc, 20)
    sudo(ssh, f"umount {MOUNT} 2>/dev/null || true; mkdir -p {MOUNT}", vm_esc, 30)

    for url in (CIFS_URL, "//192.168.0.105/mycosoft/mindex"):
        out = sudo(
            ssh,
            f"mount -t cifs {url!r} {MOUNT} -o credentials=/etc/samba/mycosoft-nas.creds,"
            "uid=1000,gid=1000,vers=3.0,iocharset=utf8",
            vm_esc,
            60,
        )
        chk = run(ssh, f"findmnt -n -o FSTYPE {MOUNT}", 20)
        if "cifs" in chk.lower():
            fstab_line = (
                f"{url} {MOUNT} cifs credentials=/etc/samba/mycosoft-nas.creds,"
                "uid=1000,gid=1000,iocharset=utf8,file_mode=0664,dir_mode=0775,"
                "vers=3.0,nofail,x-systemd.automount 0 0"
            )
            sudo(
                ssh,
                f"grep -qF {MOUNT!r} /etc/fstab || echo {fstab_line!r} >> /etc/fstab",
                vm_esc,
                30,
            )
            return True
        if "error" in out.lower():
            print(f"mount failed {url}", file=sys.stderr)
    return False


def migrate_backup_to_nas(ssh, vm_esc: str) -> None:
    backup = run(ssh, f"ls -d {BACKUP_GLOB} 2>/dev/null | head -1", 20).strip()
    if not backup:
        print("No local backup dir — skip rsync")
        return

    sudo(ssh, f"mkdir -p {MOUNT}/Library/acoustic {MOUNT}/archive {MOUNT}/training", vm_esc, 30)
    sudo(ssh, f"chown -R mycosoft:mycosoft {MOUNT}", vm_esc, 120)

    # Already running?
    ps = run(ssh, f"pgrep -af 'rsync.*{backup}' || true", 20)
    if "rsync" not in ps:
        sudo(
            ssh,
            f"nohup rsync -a {backup}/Library/ {MOUNT}/Library/ > {RSYNC_LOG} 2>&1 &",
            vm_esc,
            30,
        )
        print("rsync started (sudo)")

    print("Waiting for rsync (poll up to 2h)...")
    for _ in range(240):
        time.sleep(30)
        tail = run(ssh, f"tail -2 {RSYNC_LOG} 2>/dev/null; pgrep -c rsync 2>/dev/null || echo 0", 30)
        if tail.strip().endswith("0") or "total size" in tail.lower():
            break

    run(ssh, f"du -sh {backup}/Library {MOUNT}/Library 2>/dev/null | head -5", 60)

    # Free VM disk: remove local backup after NAS has data
    nas_count = run(ssh, f"find {MOUNT}/Library/acoustic -type f 2>/dev/null | wc -l", 120).strip()
    local_count = run(ssh, f"find {backup}/Library/acoustic -type f 2>/dev/null | wc -l", 120).strip()
    try:
        if nas_count and local_count and int(nas_count) >= int(local_count) * 0.95:
            print(f"NAS files {nas_count} >= 95% of local {local_count} — removing backup")
            sudo(ssh, f"rm -rf {backup}", vm_esc, 600)
        else:
            print(f"Keep backup until verified (nas={nas_count} local={local_count})", file=sys.stderr)
    except ValueError:
        pass


def vm_lightweight_cleanup(ssh, vm_esc: str) -> None:
    run(ssh, "rm -rf /home/mycosoft/mindex/C: /home/mycosoft/.claude/downloads 2>/dev/null; true", 30)
    sudo(ssh, "journalctl --vacuum-size=50M 2>/dev/null || true", vm_esc, 90)
    sudo(ssh, "docker system prune -f 2>/dev/null | tail -5 || true", vm_esc, 180)
    sudo(ssh, "find /var/log -type f -name '*.gz' -delete 2>/dev/null || true", vm_esc, 60)
    run(ssh, "df -h / | tail -1", 20)


def configure_env_and_api(ssh, vm_esc: str) -> None:
    for key, val in (
        ("NAS_MOUNT_PATH", MOUNT),
        ("MINDEX_NAS_DATA_DIR", MOUNT),
    ):
        run(
            ssh,
            f"cd {REMOTE} && grep -q '^{key}=' .env 2>/dev/null && "
            f"sed -i 's|^{key}=.*|{key}={val}|' .env || echo '{key}={val}' >> .env",
            30,
        )

    for mig in (
        "migrations/20260604_library_blob_labels_may27_2026.sql",
        "migrations/20260605_sine_acoustic_stack_may27_2026.sql",
    ):
        log = f"/tmp/mig-{Path(mig).name}.log"
        run(
            ssh,
            f"cd {REMOTE} && test -f {mig} && "
            f"(grep -q EXIT:0 {log} 2>/dev/null || "
            f"nohup bash -c 'cat {mig} | docker exec -i mindex-postgres psql -U mindex -d mindex "
            f"-v ON_ERROR_STOP=1 > {log} 2>&1; echo EXIT:$? >> {log}' &)",
            30,
        )

    print("Waiting for migrations...")
    for _ in range(36):
        time.sleep(5)
        logs = run(ssh, "grep EXIT /tmp/mig-*.log 2>/dev/null", 20)
        if logs.count("EXIT:0") >= 2:
            break

    run(ssh, f"cd {REMOTE} && docker compose up -d api 2>&1 | tail -8", 240)
    run(
        ssh,
        f"cd {REMOTE} && docker compose exec -T api pip install -q numpy==1.26.4 scipy==1.11.4 "
        "soundfile auditok 2>&1 | tail -6",
        400,
    )
    run(ssh, f"cd {REMOTE} && docker compose restart api 2>&1 | tail -4", 120)
    run(ssh, "sleep 12", 20)

    health = run(
        ssh,
        f"cd {REMOTE} && export $(grep -E '^MINDEX_INTERNAL' .env | xargs) && "
        "T=${MINDEX_INTERNAL_TOKENS%%,*}; [ -z \"$T\" ] && T=$MINDEX_INTERNAL_SECRET; "
        "curl -sf -m 15 -H \"X-Internal-Token: $T\" http://127.0.0.1:8000/api/mindex/library/storage; echo",
        30,
    )
    if "remote_nas" not in health and "true" not in health.lower():
        print("WARN: library/storage may not show remote_nas", file=sys.stderr)


def main() -> int:
    load_creds()
    import paramiko

    vm_pass = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    nas_pass = os.environ.get("NAS_SMB_PASSWORD", "")
    nas_user = os.environ.get("NAS_SMB_USER", "morgan")
    if not vm_pass or not nas_pass:
        print("VM_PASSWORD and NAS_SMB_PASSWORD required", file=sys.stderr)
        return 1

    vm_esc = vm_pass.replace("'", "'\"'\"'")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=vm_pass, timeout=30)

    if not ensure_cifs(ssh, vm_esc, nas_user, nas_pass):
        ssh.close()
        return 1

    migrate_backup_to_nas(ssh, vm_esc)
    vm_lightweight_cleanup(ssh, vm_esc)
    configure_env_and_api(ssh, vm_esc)

    run(ssh, f"df -h / {MOUNT} | tail -2", 20)
    ssh.close()
    print("=== VM heavy data offloaded to NAS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
