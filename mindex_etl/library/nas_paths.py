from __future__ import annotations

import os
from pathlib import Path

from .nas_mount import nas_data_root


def library_acoustic_root() -> Path:
    """Canonical NAS path for playable Library acoustic files (on CIFS/NFS mount)."""
    for key in (
        "MINDEX_LIBRARY_ROOT",
        "MINDEX_NAS_LIBRARY_ROOT",
        "NAS_LIBRARY_ROOT",
    ):
        raw = os.environ.get(key, "").strip()
        if raw:
            base = Path(raw)
            if base.name.lower() == "acoustic":
                return base
            if base.name.lower() == "library":
                return base / "acoustic"
            return base / "Library" / "acoustic"
    return nas_data_root() / "Library" / "acoustic"


def archive_acoustic_root() -> Path:
    return nas_data_root() / "archive" / "library" / "acoustic"


def training_acoustic_root() -> Path:
    return nas_data_root() / "training" / "acoustic"


def ensure_category_dirs() -> dict[str, Path]:
    roots = {
        "library": library_acoustic_root(),
        "archive": archive_acoustic_root(),
        "training": training_acoustic_root(),
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    return roots
