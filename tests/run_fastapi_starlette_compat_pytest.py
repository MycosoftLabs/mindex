"""Run pytest with a narrow test-only bridge for the local FastAPI/Starlette mismatch.

FastAPI 0.111.1 passes the legacy ``on_startup`` and ``on_shutdown`` keywords
to Starlette's Router. The locally installed Starlette 1.7.0 removed those
constructor parameters. This runner accepts and discards only empty legacy
handler values; nonempty lifecycle handlers fail loudly. It changes no product
code or installed dependency and does not bypass route or database logic.
"""

from __future__ import annotations

import inspect
import sys
from functools import wraps
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from starlette.routing import Router  # noqa: E402


def _install_empty_lifecycle_compatibility() -> None:
    parameters = inspect.signature(Router.__init__).parameters
    if "on_startup" in parameters and "on_shutdown" in parameters:
        return

    original_init = Router.__init__

    @wraps(original_init)
    def compatible_init(self, *args, on_startup=None, on_shutdown=None, **kwargs):
        if on_startup not in (None, ()) and on_startup != []:
            raise RuntimeError("Compatibility runner refuses nonempty on_startup handlers")
        if on_shutdown not in (None, ()) and on_shutdown != []:
            raise RuntimeError("Compatibility runner refuses nonempty on_shutdown handlers")
        result = original_init(self, *args, **kwargs)
        # FastAPI 0.111.1 expects Router to expose these lists; Starlette 1.7.0
        # removed them with the constructor parameters. Preserve the exact
        # empty state accepted above for FastAPI's include_router implementation.
        self.on_startup = []
        self.on_shutdown = []
        return result

    Router.__init__ = compatible_init


def main() -> int:
    _install_empty_lifecycle_compatibility()
    import pytest

    return int(pytest.main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
