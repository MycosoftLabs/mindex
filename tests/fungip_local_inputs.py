"""Explicit private qualification inputs; never download or manufacture captured data."""
import os
from pathlib import Path
from unittest import SkipTest


def local_input(name, *, directory=False):
    value = os.environ.get(name)
    if not value:
        raise SkipTest(f"Captured-data qualification requires explicit {name}; no captured data is bundled")
    path = Path(value).expanduser().resolve()
    if not (path.is_dir() if directory else path.is_file()):
        raise AssertionError(f"Configured {name} input is unavailable")
    return path
