"""Detect whether MINDEX library paths are on real NAS (CIFS/NFS), not local VM disk."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


def nas_data_root() -> Path:
    """Root of MINDEX data on NAS (mount point)."""
    raw = (
        os.environ.get("MINDEX_NAS_DATA_DIR", "").strip()
        or os.environ.get("NAS_MOUNT_PATH", "").strip()
        or os.environ.get("NAS_DATA_DIR", "").strip()
        or "/mnt/nas/mindex"
    )
    return Path(raw)


def mount_filesystem_type(mount_point: Path) -> Optional[str]:
    """Return fstype from findmnt (cifs, nfs, ext4, ...) or None."""
    try:
        proc = subprocess.run(
            ["findmnt", "-n", "-o", "FSTYPE", str(mount_point)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().lower()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def is_remote_nas_mount(mount_point: Optional[Path] = None) -> bool:
    """True when mount_point is CIFS/NFS (real NAS), not a directory on VM root disk."""
    root = mount_point or nas_data_root()
    fstype = mount_filesystem_type(root)
    return fstype in {"cifs", "smb3", "nfs", "nfs4"}


def require_nas_mount(mount_point: Optional[Path] = None) -> Path:
    """Raise RuntimeError if library data would land on local VM disk."""
    root = mount_point or nas_data_root()
    if not root.is_dir():
        raise RuntimeError(
            f"MINDEX NAS not mounted at {root}. "
            "Mount //192.168.0.105/mycosoft.com/mindex (or mycosoft/mindex) before ingest."
        )
    if not is_remote_nas_mount(root):
        fstype = mount_filesystem_type(root) or "local-dir"
        raise RuntimeError(
            f"{root} is not a NAS mount (fstype={fstype}). "
            "Ingest must not fill the VM disk — run scripts/setup_mindex_nas_mount.sh on VM 189."
        )
    return root


def nas_usage_gb(mount_point: Optional[Path] = None) -> dict[str, float | bool]:
    root = mount_point or nas_data_root()
    try:
        usage = os.statvfs(root)
        total = usage.f_frsize * usage.f_blocks
        free = usage.f_frsize * usage.f_bavail
        used = total - free
        return {
            "available": True,
            "mount_point": str(root),
            "remote_nas": is_remote_nas_mount(root),
            "fstype": mount_filesystem_type(root) or "unknown",
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
        }
    except OSError:
        return {"available": False, "mount_point": str(root), "remote_nas": False}
