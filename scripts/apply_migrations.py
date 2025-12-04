#!/usr/bin/env python
"""
Apply MINDEX SQL migrations in lexical order.

Usage:
    python scripts/apply_migrations.py --dsn postgresql://mindex:mindex@localhost:5432/mindex
Environment:
    DATABASE_URL - default DSN if --dsn is not provided.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply MINDEX SQL migrations.")
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL", "postgresql://mindex:mindex@localhost:5432/mindex"),
        help="Database connection string (default: %(default)s or DATABASE_URL)",
    )
    parser.add_argument(
        "--driver",
        choices=("psycopg", "psql"),
        default="psycopg",
        help="Preferred driver. Falls back automatically if unavailable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the migrations without executing them.",
    )
    return parser.parse_args()


def discover_migrations() -> List[Path]:
    if not MIGRATIONS_DIR.exists():
        raise SystemExit(f"No migrations directory found at {MIGRATIONS_DIR}")
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply_with_psycopg(dsn: str, files: Iterable[Path]) -> None:
    try:
        import psycopg  # type: ignore
    except ImportError as exc:  # pragma: no cover - graceful fallback
        raise RuntimeError(
            "psycopg is not installed. Install it or run with --driver psql."
        ) from exc

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for path in files:
                sql = path.read_text(encoding="utf-8")
                print(f"Applying {path.name} ...", flush=True)
                cur.execute(sql)


def apply_with_psql(dsn: str, files: Iterable[Path]) -> None:
    if shutil.which("psql") is None:
        raise RuntimeError("psql executable not found in PATH.")

    for path in files:
        print(f"Applying {path.name} via psql ...", flush=True)
        cmd = [
            "psql",
            dsn,
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            str(path),
        ]
        subprocess.run(cmd, check=True)


def main() -> None:
    args = parse_args()
    files = discover_migrations()
    if not files:
        raise SystemExit("No migration files found.")

    if args.dry_run:
        for path in files:
            print(path.name)
        return

    driver = args.driver
    try:
        if driver == "psql":
            apply_with_psql(args.dsn, files)
        else:
            apply_with_psycopg(args.dsn, files)
    except RuntimeError as err:
        if driver == "psycopg":
            print(f"{err}. Falling back to psql...", file=sys.stderr)
            apply_with_psql(args.dsn, files)
        else:
            raise

    print("Migrations applied successfully.")


if __name__ == "__main__":
    main()
