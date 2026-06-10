#!/usr/bin/env python3
"""Repair CIFS mount on MINDEX VM 189 after library was moved off local path."""
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


def main() -> int:
    load_creds()
    import paramiko

    password = os.environ.get("VM_PASSWORD") or os.environ.get("VM_SSH_PASSWORD", "")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("192.168.0.189", username="mycosoft", password=password, timeout=30)
    def run(cmd: str, timeout: int = 300, *, hide: bool = False) -> str:
        print(">>>", "[sudo]" if hide else cmd[:120])
        _, o, e = ssh.exec_command(cmd, timeout=timeout)
        out = o.read().decode(errors="replace")
        err = e.read().decode(errors="replace")
        if out:
            print(out[-2500:])
        if err:
            print(err[-800:], file=sys.stderr)
        return out

    def sudo_cmd(inner: str) -> str:
        return f"echo '{password}' | sudo -S {inner}"

    backup = run("ls -d /var/lib/mindex-nas-local-backup-* 2>/dev/null | tail -1").strip()
    if backup:
        run(sudo_cmd("umount /mnt/nas/mindex 2>/dev/null || true"), hide=True)
        run(sudo_cmd(f"mkdir -p /mnt/nas/mindex"), hide=True)
        out = run(
            sudo_cmd(f"mount --bind {backup} /mnt/nas/mindex && findmnt /mnt/nas/mindex"),
            hide=True,
        )
        if "bind" in out or backup in out:
            print(f"Restored library via bind mount from {backup} (temporary until CIFS works)")

    run(sudo_cmd("apt-get install -y cifs-utils keyutils 2>&1 | tail -5"), hide=True)
    run(sudo_cmd("modprobe cifs 2>/dev/null; mkdir -p /mnt/nas/mindex"), hide=True)

    for url in (
        "//192.168.0.105/mycosoft.com/mindex",
        "//192.168.0.105/mycosoft/mindex",
    ):
        out = run(
            sudo_cmd(
                f"mount -t cifs '{url}' /mnt/nas/mindex "
                f"-o credentials=/etc/samba/mycosoft-nas.creds,uid=1000,gid=1000,vers=3.0 "
                f"2>&1"
            ),
            hide=True,
        )
        if "error" not in out.lower() and "denied" not in out.lower() and "mount" not in out.lower():
            run("findmnt /mnt/nas/mindex; df -h /mnt/nas/mindex")
            if backup:
                run(
                    sudo_cmd(
                        f"nohup rsync -a {backup}/Library/ /mnt/nas/mindex/Library/ "
                        f"> /tmp/mindex-nas-rsync.log 2>&1 &"
                    ),
                    hide=True,
                )
                print("rsync to NAS started in background on VM (see /tmp/mindex-nas-rsync.log)")
            run("cd /home/mycosoft/mindex && docker compose restart api 2>&1 | tail -3")
            break
    else:
        print(
            "CIFS mount failed (permission denied). Update /etc/samba/mycosoft-nas.creds on VM 189 "
            "or set NAS_SMB_PASSWORD in .credentials.local and re-run apply_mindex_nas_mount.py. "
            "Library is temporarily available via bind mount from backup."
        )
    run("cd /home/mycosoft/mindex && docker compose restart api 2>&1 | tail -3")
    ssh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
