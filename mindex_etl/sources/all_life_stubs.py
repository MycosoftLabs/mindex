"""
Shared helpers for all-life planned ETL sources (MINDEX).

Each connector in this phase returns a no-op result until the real API client is
wired (see MAS repo docs/ALL_LIFE_ANCESTRY_EXPANSION_* and all-life ancestry plan).
No mock rows are written to Postgres — only explicit status payloads for orchestration.
"""

from __future__ import annotations

from typing import Any


def stub_ingest(source: str) -> dict[str, Any]:
    return {
        "ok": False,
        "source": source,
        "status": "not_implemented",
        "message": "Register connector job and implement per ALL-LIFE Ancestry expansion; no data written yet.",
    }


def not_implemented(source: str):
    def _(*_a: Any, **_k: Any) -> dict[str, Any]:
        return stub_ingest(source)

    _.__name__ = f"sync_{source}"
    return _
