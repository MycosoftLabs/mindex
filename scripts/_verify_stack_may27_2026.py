#!/usr/bin/env python3
"""Verify MAS, MINDEX, MQTT, NAS for Codex handoff."""
from __future__ import annotations

import json
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


def ssh_run(host: str, password: str, cmd: str, timeout: int = 90) -> str:
    import paramiko

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username="mycosoft", password=password, timeout=30)
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    ssh.close()
    return (out + err).strip()


def main() -> int:
    load_creds()
    vm = os.environ.get("VM_PASSWORD", "")
    if not vm:
        print("VM_PASSWORD missing", file=sys.stderr)
        return 1

    report: dict[str, object] = {"mas": {}, "mindex": {}, "mqtt": {}}

    mas_health = ssh_run("192.168.0.188", vm, "curl -sf -m 8 http://127.0.0.1:8001/health 2>/dev/null || echo FAIL", 20)
    mas_ps = ssh_run("192.168.0.188", vm, "docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | head -20", 30)
    mas_mqtt = ssh_run(
        "192.168.0.188",
        vm,
        "ss -tlnp 2>/dev/null | grep -E ':1883|:8883' || echo 'no_broker_ports_on_188'",
        20,
    )
    report["mas"] = {"health": mas_health[:800], "containers": mas_ps, "mqtt_ports": mas_mqtt}

    tok_line = ssh_run("192.168.0.189", vm, "grep ^MINDEX_INTERNAL_TOKENS= /home/mycosoft/mindex/.env | head -1", 15)
    tok = tok_line.split("=", 1)[-1].strip() if "=" in tok_line else ""
    safe = tok.replace('"', '\\"')
    for path in ("/api/mindex/health", "/api/mindex/library/storage", "/api/mindex/sine/status", "/api/mindex/console"):
        curl = (
            f'curl -sf -m 20 -H "X-Internal-Token: {safe}" '
            f'"http://127.0.0.1:8000{path}" 2>/dev/null || echo FAIL_{path}'
        )
        report.setdefault("mindex", {})[path] = ssh_run("192.168.0.189", vm, curl, 30)[:600]

    mindex_ps = ssh_run("192.168.0.189", vm, "docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | head -15", 30)
    nas = ssh_run("192.168.0.189", vm, "findmnt -n -o FSTYPE,SOURCE /mnt/nas/mindex; du -sh /mnt/nas/mindex/Library 2>/dev/null", 120)
    disk = ssh_run("192.168.0.189", vm, "pgrep -c rsync 2>/dev/null; df -h / | tail -1; test -d /var/lib/mindex-nas-local-backup-20260604005520 && echo backup_present || echo backup_gone", 60)
    mindex_mqtt = ssh_run(
        "192.168.0.189",
        vm,
        "ss -tlnp 2>/dev/null | grep -E ':1883|:8883' || echo 'no_broker_ports_on_189'",
        20,
    )
    report["mindex"]["containers"] = mindex_ps
    report["mindex"]["nas"] = nas
    report["mindex"]["disk_rsync"] = disk
    report["mqtt"]["mindex_189"] = mindex_mqtt
    report["mqtt"]["mas_188"] = mas_mqtt

    out_path = ROOT / "docs" / "STACK_VERIFY_MAY27_2026.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
