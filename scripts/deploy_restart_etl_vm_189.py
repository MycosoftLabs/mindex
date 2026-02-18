#!/usr/bin/env python3
"""
Deploy + restart the MINDEX aggressive ETL on VM 189.

Avoids PowerShell quoting issues by using Paramiko directly.

Requires:
  - env var VM_PASSWORD (ssh password for mycosoft@192.168.0.189)
"""

from __future__ import annotations

import os
import time

import paramiko


VM_IP = "192.168.0.189"
VM_USER = "mycosoft"


def _read_text(stdout: paramiko.ChannelFile) -> str:
    return stdout.read().decode("utf-8", errors="replace")


def main() -> int:
    vm_password = os.environ.get("VM_PASSWORD")
    if not vm_password:
        print("ERROR: VM_PASSWORD not set")
        return 1

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(VM_IP, username=VM_USER, password=vm_password, timeout=30)

    def run(cmd: str, timeout: int = 120) -> tuple[str, str]:
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
        return _read_text(stdout), _read_text(stderr)

    def run_fire_and_forget(cmd: str, timeout: int = 30) -> None:
        """
        Run a command where we don't want to block on stdout/stderr reads.

        Needed for background/nohup commands which can keep the channel open.
        """
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
        try:
            stdout.channel.close()
        except Exception:
            pass
        try:
            stderr.channel.close()
        except Exception:
            pass

    out, err = run("cd /home/mycosoft/mindex && git pull origin main", timeout=180)
    print(out.strip())
    if err.strip():
        print(err.strip())

    # Ensure XLSX parsing dependency exists (ignore noisy pip output).
    run("pip3 install --user openpyxl >/tmp/pip_openpyxl.out 2>/tmp/pip_openpyxl.err || true", timeout=300)

    out, _ = run(
        "python3 -c 'import importlib.util; print(\"openpyxl_ok=\"+str(bool(importlib.util.find_spec(\"openpyxl\"))))'",
        timeout=60,
    )
    print(out.strip())

    # Restart runner cleanly.
    out, _ = run("pgrep -f mindex_etl.aggressive_runner || true", timeout=30)
    pids = [p.strip() for p in out.split() if p.strip().isdigit()]
    for pid in pids:
        run(f"kill -KILL {pid} || true", timeout=10)
    time.sleep(1)

    run_fire_and_forget("cd /home/mycosoft/mindex && nohup ./start_etl.sh >> etl.log 2>&1 &", timeout=30)
    time.sleep(2)

    out, _ = run("pgrep -af mindex_etl.aggressive_runner || true", timeout=30)
    print(out.strip())

    # Show whether the MycoBank dump file is appearing.
    out, _ = run("ls -lh /home/mycosoft/mindex/data/mindex_scrape/mycobank 2>/dev/null || true", timeout=30)
    if out.strip():
        print("MycoBank dump dir listing:")
        print(out.strip())

    out, _ = run("tail -200 /home/mycosoft/mindex/etl.log", timeout=30)
    # Only print lines relevant to the restart + MycoBank
    for ln in out.splitlines():
        low = ln.lower()
        if (
            "aggressive_runner" in low
            or "phase 1" in low
            or "mycobank" in low
            or "mblist" in low
            or "using data dump" in low
            or "failed to parse dump" in low
            or "openpyxl" in low
            or "trying:" in low and "mycobank" in low
            or "downloading" in low and "mblist" in low
        ):
            print(ln)

    ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

